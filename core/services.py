# D:\Project Archive\GAT\core\services.py

import pandas as pd
import re
import os
import uuid
from collections import defaultdict
from datetime import datetime

from django.db import transaction, models, IntegrityError
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage

from .models import (
    Student, StudentResult, GatTest, Question, 
    SchoolClass, Subject, StudentAnswer
)
from .utils import calculate_grade_from_percentage 

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

def normalize_cyrillic(text):
    """
    Заменяет латинские буквы, похожие на кириллицу, на их кириллические аналоги.
    """
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    
    mapping = {
        'A': 'А', 'a': 'а', 'B': 'В', 'b': 'в', 'E': 'Е', 'e': 'е',
        'K': 'К', 'k': 'к', 'M': 'М', 'm': 'м', 'H': 'Н', 'h': 'н',
        'O': 'О', 'o': 'о', 'P': 'Р', 'p': 'р', 'C': 'С', 'c': 'с',
        'T': 'Т', 't': 'т', 'X': 'Х', 'x': 'х', 'y': 'у', 'Y': 'У'
    }
    
    result = []
    for char in text:
        result.append(mapping.get(char, char))
    
    return "".join(result).strip()

def normalize_header(text):
    """Очистка заголовков Excel."""
    if not isinstance(text, str):
        return str(text)
    return text.lower().strip()

def extract_test_date_from_excel(uploaded_file):
    """Пытается извлечь дату теста из имени файла."""
    filename = uploaded_file.name
    date_patterns = [
        r'(\d{4})[-_](\d{2})[-_](\d{2})',  # 2025-12-15
        r'(\d{2})[-_\.](\d{2})[-_\.](\d{4})' # 15-12-2025
    ]
    for pattern in date_patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:
                    return datetime.strptime(f"{groups[0]}-{groups[1]}-{groups[2]}", "%Y-%m-%d").date()
                else:
                    return datetime.strptime(f"{groups[2]}-{groups[1]}-{groups[0]}", "%Y-%m-%d").date()
            except ValueError:
                continue
    return None

def _get_or_create_student_smart(row_data, test_school_class, test_school, update_names=True):
    """
    Ищет студента. Если в Excel указана ПАРАЛЛЕЛЬ (например, 7),
    а ученик числится в ПОДКЛАССЕ (например, 7А), этот код его НАЙДЕТ.
    """
    raw_id = row_data.get('student_id')
    student_id = str(raw_id).strip() if pd.notna(raw_id) else None
    if student_id and student_id.endswith('.0'): student_id = student_id[:-2]
    if student_id and student_id.lower() in ['nan', 'none', '', '0']: student_id = None
    if student_id and student_id.isdigit() and len(student_id) < 6: student_id = student_id.zfill(6)

    last_name = normalize_cyrillic(str(row_data.get('last_name', '')).strip()).title()
    first_name = normalize_cyrillic(str(row_data.get('first_name', '')).strip()).title()
    excel_class_name = str(row_data.get('class_name', '')).strip()

    # 1. Поиск по ID
    if student_id:
        student = Student.objects.filter(student_id=student_id).first()
        if student:
            updated = False
            if update_names:
                if last_name and (student.last_name_ru != last_name):
                    student.last_name_ru = last_name
                    updated = True
                if first_name and (student.first_name_ru != first_name):
                    student.first_name_ru = first_name
                    updated = True
                if updated: student.save()
            return student, False, updated

    # 2. Поиск по ФИО и Классу
    if last_name and first_name:
        classes_to_search = [test_school_class]
        if test_school_class.parent is None:
            subclasses = test_school_class.subclasses.all()
            classes_to_search.extend(subclasses)

        student = Student.objects.filter(
            school_class__in=classes_to_search,
            last_name_ru__iexact=last_name,
            first_name_ru__iexact=first_name
        ).first()

        if student:
            updated = False
            if student_id and student.student_id != student_id:
                student.student_id = student_id
                updated = True
                student.save()
            return student, False, updated

    # 3. Создание нового
    final_id = student_id if student_id else f"TEMP-{uuid.uuid4().hex[:8].upper()}"
    
    target_class = test_school_class
    if excel_class_name:
        norm_excel_class = normalize_cyrillic(excel_class_name).upper()
        found_class = SchoolClass.objects.filter(school=test_school, name__iexact=norm_excel_class, parent=test_school_class).first()
        
        if not found_class and test_school_class.parent is None:
            combined_name = f"{test_school_class.name}{norm_excel_class}"
            found_class = SchoolClass.objects.filter(school=test_school, name__iexact=combined_name, parent=test_school_class).first()

        if not found_class:
             found_class = SchoolClass.objects.filter(school=test_school, name__iexact=norm_excel_class).first()

        if found_class:
            target_class = found_class

    try:
        new_student = Student.objects.create(
            student_id=final_id,
            school_class=target_class,
            last_name_ru=last_name if last_name else "Unknown",
            first_name_ru=first_name if first_name else "Unknown",
            status='ACTIVE'
        )
        return new_student, True, False
    except IntegrityError:
        try:
            return Student.objects.get(student_id=final_id), False, False
        except Student.DoesNotExist:
            raise

# =============================================================================
# --- ФУНКЦИИ АНАЛИЗА И ЗАГРУЗКИ ---
# =============================================================================

def analyze_student_results(excel_file):
    """Анализ файла при загрузке списка студентов (через меню Студенты)."""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    temp_file_name = f"temp_gat_upload_{timestamp}.xlsx"
    file_path = default_storage.save(f"temp/{temp_file_name}", excel_file)
    
    try:
        full_path = default_storage.path(file_path)
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        if default_storage.exists(file_path): default_storage.delete(file_path)
        return {'error': f"Ошибка чтения файла: {e}"}

    df.columns = [normalize_header(col) for col in df.columns]
    # ... (упрощенная логика маппинга для краткости, она дублируется в process) ...
    return {'total_rows': len(df), 'file_path': file_path}


# ✨ ВОТ ЭТА ФУНКЦИЯ БЫЛА ПОТЕРЯНА. ОНА НУЖНА ДЛЯ UPLOAD_RESULTS_VIEW ✨
def analyze_results_file(gat_test, file_path):
    """
    Анализирует файл результатов GAT перед загрузкой.
    """
    full_path = default_storage.path(file_path)
    try:
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        return {'error': f"Ошибка чтения Excel: {e}"}

    df.columns = [normalize_header(col) for col in df.columns]
    
    mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'id ученика': 'student_id',
        'surname': 'last_name', 'фамилия': 'last_name', 'lastname': 'last_name',
        'name': 'first_name', 'имя': 'first_name', 'firstname': 'first_name',
        'section': 'class_name', 'class': 'class_name', 'класс': 'class_name', 'grade': 'class_name'
    }
    df.rename(columns=mapping, inplace=True)

    analysis = {
        'total_rows': len(df),
        'new_students': [],
        'name_conflicts': [],
        'transfers': [],
        'error': None
    }

    school = gat_test.school
    
    # === ДОБАВЛЕНО: Определяем префикс класса (например, "8") ===
    test_grade_prefix = ""
    if gat_test.school_class:
        if gat_test.school_class.parent:
            test_grade_prefix = gat_test.school_class.parent.name
        else:
            test_grade_prefix = gat_test.school_class.name
    # ============================================================

    existing_students = {s.student_id: s for s in Student.objects.filter(school_class__school=school)}
    processed_ids = set()

    for index, row in df.iterrows():
        raw_id = row.get('student_id')
        if pd.isna(raw_id) or str(raw_id).strip().lower() in ['nan', 'none', '', '0']: continue
        
        st_id = str(raw_id).strip()
        if st_id.endswith('.0'): st_id = st_id[:-2]
        st_id = st_id.zfill(6)
        
        if st_id in processed_ids: continue
        processed_ids.add(st_id)

        excel_last = normalize_cyrillic(str(row.get('last_name', '')).strip()).title()
        excel_first = normalize_cyrillic(str(row.get('first_name', '')).strip()).title()
        if excel_last.lower() == 'nan': excel_last = ''
        if excel_first.lower() == 'nan': excel_first = ''
        
        excel_class = str(row.get('class_name', '')).strip()

        if st_id in existing_students:
            student = existing_students[st_id]
            # Конфликты имен
            if excel_last and excel_first:
                if student.last_name_ru != excel_last or student.first_name_ru != excel_first:
                    analysis['name_conflicts'].append({
                        'id': st_id,
                        'current_name': f"{student.last_name_ru} {student.first_name_ru}",
                        'new_name': f"{excel_last} {excel_first}",
                        'class': student.school_class.name
                    })
            
            # Переводы
            if excel_class:
                norm_excel = normalize_cyrillic(excel_class).replace(" ", "").upper()
                
                # === ИСПРАВЛЕНИЕ: Если в Excel только "Б", добавляем "8" -> "8Б" ===
                if len(norm_excel) == 1 and test_grade_prefix:
                    norm_excel = (test_grade_prefix + norm_excel).upper()
                # ===================================================================

                norm_db = normalize_cyrillic(student.school_class.name).replace(" ", "").upper()
                
                if norm_excel != norm_db:
                     analysis['transfers'].append({
                        'id': st_id,
                        'name': f"{student.last_name_ru} {student.first_name_ru}",
                        'old_class': student.school_class.name,
                        'new_class': norm_excel # Показываем полный класс (8Б), а не просто Б
                     })
        else:
            # Для новых тоже формируем красивое имя класса
            display_class = excel_class
            if len(display_class) == 1 and test_grade_prefix:
                display_class = test_grade_prefix + display_class

            analysis['new_students'].append({
                'id': st_id,
                'name': f"{excel_last} {excel_first}",
                'class': display_class or '?'
            })
            
    return analysis


def process_student_results_upload(gat_test, excel_file_path, overrides_map=None):
    """
    Основная функция загрузки результатов GAT.
    ВКЛЮЧАЕТ ЗАЩИТУ ОТ ДУБЛИКАТОВ СТРОК.
    """
    if overrides_map is None:
        overrides_map = {}

    try:
        full_path = default_storage.path(excel_file_path)
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        return False, {'errors': [f"Ошибка чтения файла по пути {excel_file_path}: {e}"]}

    df.columns = [normalize_header(col) for col in df.columns]
    column_mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'рамз': 'student_id', 'id ученика': 'student_id',
        'surname': 'last_name', 'фамилия': 'last_name', 'насаб': 'last_name', 
        'lastname': 'last_name', 'surname_en': 'last_name',
        'name': 'first_name', 'имя': 'first_name', 'ном': 'first_name', 
        'firstname': 'first_name', 'name_en': 'first_name',
        'section': 'class_name', 'class': 'class_name', 'класс': 'class_name', 
        'синф': 'class_name', 'grade': 'class_name'
    }
    df.rename(columns=column_mapping, inplace=True)

    # === ИСПРАВЛЕНИЕ: Удаляем дубликаты по ID ученика ===
    # Если ученик встречается дважды, оставляем последнего (keep='last')
    if 'student_id' in df.columns:
        # Сначала чистим ID от мусора, чтобы точно найти дубли
        df['student_id'] = df['student_id'].astype(str).str.strip()
        df = df[~df['student_id'].isin(['nan', 'none', '', '0'])] # Убираем пустые
        df.drop_duplicates(subset=['student_id'], keep='last', inplace=True)
    # ====================================================

    created_students = 0
    updated_names = 0
    results_processed = 0
    errors = []
    
    batch_grades = []

    subjects_map = {}
    for s in gat_test.subjects.all():
        subjects_map[normalize_cyrillic(s.name.strip().lower())] = s
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    max_test_score = gat_test.questions.aggregate(total=models.Sum('points'))['total'] or 0

    student_answers_to_create = [] 
    processed_result_ids = []

    test_school = gat_test.school
    test_school_class = gat_test.school_class

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            row_dict = row.to_dict()

            # --- 1. Ищем или создаем студента ---
            student, created, updated = _get_or_create_student_smart(
                row_dict, 
                test_school_class, 
                test_school, 
                update_names=False
            )
            
            if created: created_students += 1
            if updated: updated_names += 1
            if not student:
                 errors.append(f"Строка {row_num}: Не удалось создать/найти студента.")
                 continue

            # --- 2. Обработка конфликтов имен ---
            excel_last = normalize_cyrillic(str(row.get('last_name', '')).strip())
            excel_first = normalize_cyrillic(str(row.get('first_name', '')).strip())
            
            if excel_last and excel_first:
                decision = overrides_map.get(student.student_id, 'db')
                if decision == 'excel':
                    if student.last_name_ru != excel_last or student.first_name_ru != excel_first:
                        student.last_name_ru = excel_last
                        student.first_name_ru = excel_first
                        student.save()
                        updated_names += 1

            # --- 3. Подсчет баллов ---
            scores_by_subject = {} 
            total_score = 0
            
            current_student_answers_data = defaultdict(dict)

            for col_name in df.columns:
                if '_' not in col_name: continue
                parts = col_name.rsplit('_', 1)
                if len(parts) != 2: continue
                
                subj_name_raw, q_num_str = parts
                subj_name_norm = normalize_cyrillic(subj_name_raw.lower())
                
                if subj_name_norm not in subjects_map: continue
                if not q_num_str.isdigit(): continue

                subject = subjects_map[subj_name_norm]
                q_num = int(q_num_str)
                
                try:
                    val_str = str(row[col_name]).replace(',', '.')
                    val = float(val_str)
                    is_correct = (val > 0)
                except (ValueError, TypeError):
                    is_correct = False

                if is_correct: 
                    total_score += 1

                if str(subject.id) not in scores_by_subject: scores_by_subject[str(subject.id)] = {}
                scores_by_subject[str(subject.id)][str(q_num)] = is_correct
                
                current_student_answers_data[subject][q_num] = is_correct

            # --- 4. Сохранение результата ---
            student_result, _ = StudentResult.objects.update_or_create(
                student=student,
                gat_test=gat_test,
                defaults={'total_score': total_score, 'scores_by_subject': scores_by_subject}
            )
            processed_result_ids.append(student_result.id)

            # --- 5. Подготовка StudentAnswer ---
            for subject, answers in current_student_answers_data.items():
                for q_num, is_correct in answers.items():
                    question = Question.objects.filter(
                        gat_test=gat_test, subject=subject, question_number=q_num
                    ).first()
                    
                    if not question:
                        question = Question.objects.create(
                            gat_test=gat_test, subject=subject, question_number=q_num
                        )
                    
                    student_answers_to_create.append(StudentAnswer(
                        result=student_result,
                        question=question,
                        is_correct=is_correct
                    ))

            if max_test_score > 0:
                percent = (total_score / max_test_score) * 100
                grade = calculate_grade_from_percentage(percent)
                batch_grades.append(grade)
            
            results_processed += 1

    # ✨ ОПТИМИЗАЦИЯ BULK
    if processed_result_ids:
        # Удаляем старые ответы перед записью новых
        StudentAnswer.objects.filter(result_id__in=processed_result_ids).delete()
    
    if student_answers_to_create:
        # Теперь безопасно создаем новые, так как дубликатов студентов нет
        StudentAnswer.objects.bulk_create(
    student_answers_to_create, 
    batch_size=2000, 
    ignore_conflicts=True  # <--- ВАЖНОЕ ДОБАВЛЕНИЕ
)

    if default_storage.exists(excel_file_path):
        default_storage.delete(excel_file_path)

    avg_grade_batch = 0
    if batch_grades:
        avg_grade_batch = round(sum(batch_grades) / len(batch_grades), 2)

    return True, {
        'total_unique_students': results_processed,
        'created_students': created_students,
        'updated_names': updated_names,
        'average_batch_grade': avg_grade_batch,
        'errors': errors
    }

def process_student_upload(excel_file, school=None):
    """
    Загрузка списка студентов (импорт базы).
    """
    try:
        df = pd.read_excel(excel_file, dtype=str)
    except Exception as e:
        return {'errors': [f"Ошибка чтения: {e}"]}

    df.columns = [normalize_header(col) for col in df.columns]
    mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'id ученика': 'student_id',
        'surname': 'last_name', 'фамилия': 'last_name',
        'name': 'first_name', 'имя': 'first_name',
        'class': 'class_name', 'класс': 'class_name', 'grade': 'class_name'
    }
    df.rename(columns=mapping, inplace=True)

    if 'student_id' not in df.columns:
        return {'errors': ["Нет колонки ID"]}

    created_cnt = 0
    updated_cnt = 0
    errors = []

    # Кеш классов
    classes_qs = SchoolClass.objects.all()
    if school: classes_qs = classes_qs.filter(school=school)
    classes_cache = {}
    for c in classes_qs:
        norm = normalize_cyrillic(c.name).replace(" ", "").upper()
        if norm not in classes_cache: classes_cache[norm] = []
        classes_cache[norm].append(c)

    with transaction.atomic():
        for index, row in df.iterrows():
            row_dict = row.to_dict()
            # Для импорта базы используем ту же умную функцию, но с update_names=True
            # так как цель именно обновить базу
            # test_school_class тут не нужен, передаем None, так как ищем глобально
            
            # Внимание: _get_or_create_student_smart ожидает test_school_class для поиска "внутри параллели".
            # При глобальном импорте его нет. Поэтому используем упрощенную логику или адаптируем.
            # Но для краткости и чтобы не ломать логику, оставим здесь старый добрый код загрузки
            # или адаптируем вызов smart.
            
            # --- УПРОЩЕННЫЙ ВАРИАНТ ДЛЯ ИМПОРТА БАЗЫ (чтобы не усложнять services.py еще больше) ---
            st_id = str(row_dict.get('student_id')).strip()
            if not st_id or st_id.lower() == 'nan': continue
            if st_id.endswith('.0'): st_id = st_id[:-2]
            st_id = st_id.zfill(6)

            cls_name = str(row_dict.get('class_name', '')).strip()
            cls_norm = normalize_cyrillic(cls_name).replace(" ", "").upper()
            
            target_cls = None
            if cls_norm in classes_cache:
                target_cls = classes_cache[cls_norm][0]
            
            lname = str(row_dict.get('last_name', '')).strip()
            fname = str(row_dict.get('first_name', '')).strip()

            obj, created = Student.objects.update_or_create(
                student_id=st_id,
                defaults={
                    'last_name_ru': lname,
                    'first_name_ru': fname,
                    'school_class': target_cls,
                    'status': 'ACTIVE'
                }
            )
            if created: created_cnt += 1
            else: updated_cnt += 1

    return {'created': created_cnt, 'updated': updated_cnt, 'errors': errors}