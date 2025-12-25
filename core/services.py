# D:\Project Archive\GAT\core\services.py

import pandas as pd
import re
import uuid
import logging
import openpyxl  # <--- ДОБАВЛЕНО: Нужен для чтения заголовков
from collections import defaultdict
from datetime import datetime

from django.db import transaction, models, IntegrityError
from django.core.files.storage import default_storage

from .models import (
    Student, StudentResult, GatTest, Question, 
    SchoolClass, Subject, StudentAnswer
)
from .utils import calculate_grade_from_percentage 

logger = logging.getLogger(__name__)

# =============================================================================
# --- КОНФИГУРАЦИЯ И КОНСТАНТЫ ---
# =============================================================================

COLUMN_MAPPING = {
    # ID
    'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 
    'код': 'student_id', 'рамз': 'student_id', 'id ученика': 'student_id',
    
    # Класс
    'section': 'class_name', 'class': 'class_name', 'класс': 'class_name', 
    'class name': 'class_name', 'grade': 'class_name', 'синф': 'class_name',
    
    # Фамилии
    'lastname': 'last_name_ru', 'фамилия': 'last_name_ru', 
    'фамилия (ru)': 'last_name_ru', 'фамилия (рус)': 'last_name_ru',
    'насаб': 'last_name_tj', 'nasab': 'last_name_tj',
    'surname': 'last_name_en', 'surname (en)': 'last_name_en',
    
    # Имена
    'firstname': 'first_name_ru', 'имя': 'first_name_ru', 
    'имя (ru)': 'first_name_ru', 'имя (рус)': 'first_name_ru',
    'ном': 'first_name_tj', 'nom': 'first_name_tj',
    'name': 'first_name_en', 'name (en)': 'first_name_en', 'first_name_en': 'first_name_en'
}

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (UTILS) ---
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
    return "".join(mapping.get(char, char) for char in text).strip()

def normalize_header(text):
    """Очистка заголовков Excel."""
    if not isinstance(text, str):
        return str(text)
    return text.lower().strip()

def _read_excel_to_df(file_path_or_obj):
    """
    Универсальное чтение Excel в DataFrame с нормализацией заголовков.
    """
    try:
        df = pd.read_excel(file_path_or_obj, dtype=str)
        df.columns = [normalize_header(col) for col in df.columns]
        df.rename(columns=COLUMN_MAPPING, inplace=True)
        # Убираем дубликаты колонок, если они появились после переименования
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.fillna('').replace(['nan', 'None', 'NULL'], '')
        return df, None
    except Exception as e:
        return None, f"Ошибка чтения Excel-файла: {e}"

def _get_target_class(school, class_name_raw, classes_cache):
    """
    Умный поиск класса. Если есть "10" и просят "10А" — находит или создает.
    classes_cache: словарь {'10А': class_obj, ...}
    """
    if not class_name_raw:
        return None

    class_name_norm = normalize_cyrillic(class_name_raw).replace(" ", "").upper()
    
    # 1. Прямой поиск в кеше
    if class_name_norm in classes_cache:
        return classes_cache[class_name_norm]

    # 2. Попытка создать подкласс (если есть параллель)
    # Пример: ищем "10А", находим параллель "10"
    match = re.match(r'^(\d+)([А-ЯA-Z])$', class_name_norm)
    if match and school:
        parallel_name = match.group(1) # "10"
        parent_cls = classes_cache.get(parallel_name)
        
        if parent_cls and parent_cls.parent is None: # Убедимся, что это параллель
            try:
                new_cls = SchoolClass.objects.create(
                    name=class_name_norm, # "10А"
                    school=school,
                    parent=parent_cls
                )
                classes_cache[class_name_norm] = new_cls # Обновляем кеш
                return new_cls
            except IntegrityError:
                # Гонка потоков: кто-то уже создал класс
                return SchoolClass.objects.filter(school=school, name=class_name_norm).first()
    
    return None

# =============================================================================
# --- ЛОГИКА СТУДЕНТОВ (ПОИСК И СОЗДАНИЕ) ---
# =============================================================================

def _get_or_create_student_smart(row_data, test_school_class, test_school, update_names=True):
    """
    Ищет студента по ID или ФИО+Классу. Учитывает подклассы.
    """
    # 1. Подготовка данных
    raw_id = row_data.get('student_id')
    student_id = str(raw_id).strip() if raw_id else None
    if student_id and len(student_id) < 6 and student_id.isdigit():
        student_id = student_id.zfill(6)
        
    last_name = normalize_cyrillic(row_data.get('last_name_ru', row_data.get('last_name', ''))).title()
    first_name = normalize_cyrillic(row_data.get('first_name_ru', row_data.get('first_name', ''))).title()
    excel_class_name = row_data.get('class_name', '')

    # 2. Поиск по ID (самый надежный)
    if student_id:
        student = Student.objects.filter(student_id=student_id).first()
        if student:
            updated = False
            if update_names:
                if last_name and student.last_name_ru != last_name:
                    student.last_name_ru = last_name
                    updated = True
                if first_name and student.first_name_ru != first_name:
                    student.first_name_ru = first_name
                    updated = True
                if updated: student.save()
            return student, False, updated

    # 3. Поиск по ФИО (если ID нет или не найден)
    if last_name and first_name:
        classes_to_search = [test_school_class]
        # Добавляем подклассы, если это параллель
        if test_school_class.parent is None:
            classes_to_search.extend(test_school_class.subclasses.all())

        student = Student.objects.filter(
            school_class__in=classes_to_search,
            last_name_ru__iexact=last_name,
            first_name_ru__iexact=first_name
        ).first()

        if student:
            # Если нашли, и в Excel был ID — проставим его
            updated = False
            if student_id and student.student_id != student_id:
                student.student_id = student_id
                student.save()
                updated = True
            return student, False, updated

    # 4. Создание нового
    final_id = student_id if student_id else f"TEMP-{uuid.uuid4().hex[:8].upper()}"
    
    # Определяем класс
    target_class = test_school_class
    
    # Пытаемся найти более точный класс (например, тест для 10, а ученик в 10Б)
    if excel_class_name:
        pass 

    try:
        new_student = Student.objects.create(
            student_id=final_id,
            school_class=target_class,
            last_name_ru=last_name or "Unknown",
            first_name_ru=first_name or "Unknown",
            status='ACTIVE'
        )
        return new_student, True, False
    except IntegrityError:
        # Если ID уже занят (гонка), возвращаем того, кто занял
        return Student.objects.filter(student_id=final_id).first(), False, False

# =============================================================================
# --- ОСНОВНЫЕ ФУНКЦИИ ЗАГРУЗКИ ---
# =============================================================================

def process_student_upload(excel_file, school=None):
    """
    Массовая загрузка списка учеников.
    """
    df, error = _read_excel_to_df(excel_file)
    if error: return {'errors': [error]}
    if 'student_id' not in df.columns:
        return {'errors': ["В файле нет колонки ID (Code/Код)."]}

    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'errors': []}

    # Кеширование классов школы для скорости
    classes_qs = SchoolClass.objects.select_related('parent')
    if school:
        classes_qs = classes_qs.filter(school=school)
    
    classes_cache = {}
    for c in classes_qs:
        norm_name = normalize_cyrillic(c.name).replace(" ", "").upper()
        classes_cache[norm_name] = c

    # Кеширование существующих студентов (Optimization!)
    existing_students = {}
    if school:
        students_qs = Student.objects.filter(school_class__school=school)
        for s in students_qs:
            existing_students[s.student_id] = s

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            
            raw_id = row.get('student_id')
            if not raw_id:
                stats['skipped'] += 1; continue
            
            st_id = str(raw_id).strip()
            if st_id.endswith('.0'): st_id = st_id[:-2]
            if st_id.isdigit() and len(st_id) < 6: st_id = st_id.zfill(6)
            
            class_name = row.get('class_name')
            target_class = _get_target_class(school, class_name, classes_cache)
            
            ru_last = normalize_cyrillic(row.get('last_name_ru', '')).title()
            ru_first = normalize_cyrillic(row.get('first_name_ru', '')).title()
            
            if st_id in existing_students:
                student = existing_students[st_id]
                changed = False
                
                if ru_last and student.last_name_ru != ru_last:
                    student.last_name_ru = ru_last; changed = True
                if ru_first and student.first_name_ru != ru_first:
                    student.first_name_ru = ru_first; changed = True
                
                if target_class and student.school_class != target_class:
                    is_downgrade = (student.school_class.parent == target_class)
                    if not is_downgrade:
                        student.school_class = target_class; changed = True
                
                if changed:
                    student.save()
                    stats['updated'] += 1
            
            else:
                if not target_class:
                    stats['errors'].append(f"Строка {row_num}: Класс '{class_name}' не найден.")
                    continue
                
                if not ru_last: ru_last = row.get('last_name_en') or row.get('last_name_tj') or '-'
                if not ru_first: ru_first = row.get('first_name_en') or row.get('first_name_tj') or '-'

                try:
                    Student.objects.create(
                        student_id=st_id,
                        school_class=target_class,
                        last_name_ru=ru_last,
                        first_name_ru=ru_first,
                        last_name_tj=row.get('last_name_tj', ''),
                        first_name_tj=row.get('first_name_tj', ''),
                        last_name_en=row.get('last_name_en', ''),
                        first_name_en=row.get('first_name_en', ''),
                        status='ACTIVE'
                    )
                    stats['created'] += 1
                except IntegrityError:
                    stats['errors'].append(f"Строка {row_num}: Дубликат ID {st_id}.")

    return stats


def process_student_results_upload(gat_test, excel_file_path, overrides_map=None):
    """
    Загрузка результатов GAT. Оптимизирована для скорости.
    """
    overrides_map = overrides_map or {}
    
    full_path = default_storage.path(excel_file_path)
    df, error = _read_excel_to_df(full_path)
    if error: return False, {'errors': [error]}

    subjects_map = {} 
    for s in gat_test.subjects.all():
        name_key = normalize_cyrillic(s.name.strip().lower())
        subjects_map[name_key] = s
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    max_score = gat_test.questions.aggregate(total=models.Sum('points'))['total'] or 0
    
    target_classes = [gat_test.school_class]
    if gat_test.school_class.parent is None:
        target_classes.extend(gat_test.school_class.subclasses.all())
    
    preloaded_students = {
        s.student_id: s 
        for s in Student.objects.filter(school_class__in=target_classes)
    }

    stats = {'total_unique_students': 0, 'created_students': 0, 'updated_names': 0, 'errors': []}
    student_answers_buffer = []
    processed_result_ids = []
    batch_grades = []

    with transaction.atomic():
        for index, row in df.iterrows():
            row_dict = row.to_dict()
            
            raw_id = row_dict.get('student_id')
            student = None
            
            if raw_id and str(raw_id) in preloaded_students:
                student = preloaded_students[str(raw_id)]
            else:
                student, created, updated = _get_or_create_student_smart(
                    row_dict, gat_test.school_class, gat_test.school, update_names=False
                )
                if created: stats['created_students'] += 1
                if updated: stats['updated_names'] += 1

            if not student:
                stats['errors'].append(f"Строка {index+2}: Ученик не найден и не создан.")
                continue

            excel_last = normalize_cyrillic(row_dict.get('last_name_ru', '')).title()
            excel_first = normalize_cyrillic(row_dict.get('first_name_ru', '')).title()
            
            if excel_last and excel_first:
                if overrides_map.get(student.student_id) == 'excel':
                    if student.last_name_ru != excel_last or student.first_name_ru != excel_first:
                        student.last_name_ru = excel_last
                        student.first_name_ru = excel_first
                        student.save()
                        stats['updated_names'] += 1

            scores_by_subject = {} 
            total_score = 0
            row_answers_data = defaultdict(dict)

            for col_name in df.columns:
                if '_' not in col_name: continue
                parts = col_name.rsplit('_', 1)
                if len(parts) != 2: continue
                
                subj_name_raw, q_num_str = parts
                subj_name_norm = normalize_cyrillic(subj_name_raw.lower())
                
                if subj_name_norm not in subjects_map or not q_num_str.isdigit():
                    continue

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
                row_answers_data[subject][q_num] = is_correct

            student_result, _ = StudentResult.objects.update_or_create(
                student=student,
                gat_test=gat_test,
                defaults={'total_score': total_score, 'scores_by_subject': scores_by_subject}
            )
            processed_result_ids.append(student_result.id)
            stats['total_unique_students'] += 1

            for subject, answers in row_answers_data.items():
                for q_num, is_correct in answers.items():
                    question, _ = Question.objects.get_or_create(
                        gat_test=gat_test, subject=subject, question_number=q_num
                    )
                    
                    student_answers_buffer.append(StudentAnswer(
                        result=student_result,
                        question=question,
                        is_correct=is_correct
                    ))

            if max_score > 0:
                percent = (total_score / max_score) * 100
                batch_grades.append(calculate_grade_from_percentage(percent))

    if processed_result_ids:
        StudentAnswer.objects.filter(result_id__in=processed_result_ids).delete()
    
    if student_answers_buffer:
        StudentAnswer.objects.bulk_create(student_answers_buffer, batch_size=2000)

    if default_storage.exists(excel_file_path):
        default_storage.delete(excel_file_path)

    stats['average_batch_grade'] = round(sum(batch_grades) / len(batch_grades), 2) if batch_grades else 0
    return True, stats

# =============================================================================
# --- АНАЛИЗ (PREVIEW) ---
# =============================================================================

def analyze_student_results(excel_file):
    """
    Предпросмотр файла перед загрузкой (поиск конфликтов ФИО).
    """
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    file_path = default_storage.save(f"temp/temp_gat_{timestamp}.xlsx", excel_file)
    full_path = default_storage.path(file_path)

    df, error = _read_excel_to_df(full_path)
    if error:
        default_storage.delete(file_path)
        return {'error': error}

    conflicts = []
    new_students_count = 0

    file_ids = [str(x).strip() for x in df['student_id'].unique() if x]
    db_students = {s.student_id: s for s in Student.objects.filter(student_id__in=file_ids)}

    for _, row in df.iterrows():
        st_id = str(row.get('student_id', '')).strip()
        if not st_id: continue

        excel_last = normalize_cyrillic(row.get('last_name_ru', '')).title()
        excel_first = normalize_cyrillic(row.get('first_name_ru', '')).title()

        if st_id not in db_students:
            new_students_count += 1
        else:
            student = db_students[st_id]
            if excel_last and excel_first:
                if student.last_name_ru != excel_last or student.first_name_ru != excel_first:
                    conflicts.append({
                        'student_id': st_id,
                        'current_last': student.last_name_ru,
                        'current_first': student.first_name_ru,
                        'new_last': excel_last,
                        'new_first': excel_first,
                        'class_name': student.school_class.name
                    })

    return {
        'conflicts': conflicts,
        'new_students_count': new_students_count,
        'file_path': file_path,
        'total_rows': len(df)
    }

# =============================================================================
# --- НОВАЯ ФУНКЦИЯ ДЛЯ ВАЛИДАЦИИ ДАТЫ В EXCEL ---
# =============================================================================

def extract_test_date_from_excel(file_obj):
    """
    Пытается извлечь дату теста из заголовка Excel-файла (первые 10 строк).
    Если дата не найдена — возвращает None.
    """
    try:
        # data_only=True важен, чтобы читать вычисленные значения, а не формулы
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active
        
        # Просматриваем первые 10 строк и 10 колонок
        for row in sheet.iter_rows(min_row=1, max_row=10, max_col=10):
            for cell in row:
                value = cell.value
                
                # 1. Если Excel уже считает это датой
                if isinstance(value, datetime):
                    return value.date()
                
                # 2. Если это текст, пробуем найти дату регуляркой
                if isinstance(value, str):
                    # Ищем форматы: DD.MM.YYYY или DD/MM/YYYY или YYYY-MM-DD
                    match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})|(\d{4}-\d{2}-\d{2})', value)
                    if match:
                        date_str = match.group(0).replace('/', '.').replace('-', '.')
                        # Пробуем разные форматы
                        for fmt in ('%d.%m.%Y', '%Y.%m.%d'):
                            try:
                                return datetime.strptime(date_str, fmt).date()
                            except ValueError:
                                continue
        return None
    except Exception as e:
        logger.error(f"Ошибка при извлечении даты из Excel: {e}")
        return None