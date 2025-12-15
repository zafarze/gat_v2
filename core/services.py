# D:\New_GAT\core\services.py

import pandas as pd
import re
import os
from datetime import datetime
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage
from .models import Student, StudentResult, GatTest, Question, SchoolClass, Subject

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
    """
    Очистка заголовков Excel: нижний регистр и удаление пробелов.
    """
    if not isinstance(text, str):
        return str(text)
    return text.lower().strip()

def extract_test_date_from_excel(uploaded_file):
    """
    Пытается извлечь дату теста из имени файла.
    """
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

# =============================================================================
# --- 1. ЗАГРУЗКА СПИСКА УЧЕНИКОВ (Mass Import Students) ---
# =============================================================================

def process_student_upload(excel_file):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    Используется в разделе "Ученики -> Импорт".
    """
    try:
        df = pd.read_excel(excel_file, dtype=str)
        df.columns = [normalize_header(col) for col in df.columns]
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    # Маппинг колонок
    column_mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 
        'id ученика': 'student_id', 'код': 'student_id', 'рамз': 'student_id',
        'section': 'класс', 'class': 'класс', 'класс': 'класс', 
        'class name': 'класс', 'grade': 'класс', 'синф': 'класс',
        'lastname': 'фамилия_рус', 'фамилия': 'фамилия_рус', 
        'фамилия (ru)': 'фамилия_рус', 'фамилия (рус)': 'фамилия_рус',
        'firstname': 'имя_рус', 'имя': 'имя_рус', 
        'имя (ru)': 'имя_рус', 'имя (рус)': 'имя_рус',
        'насаб': 'фамилия_tj', 'nasab': 'фамилия_tj', 'насаб (tj)': 'фамилия_tj',
        'ном': 'имя_tj', 'nom': 'имя_tj', 'ном (tj)': 'имя_tj',
        'surname': 'фамилия_en', 'surname (en)': 'фамилия_en', 'surname(en)': 'фамилия_en', 
        'surname en': 'фамилия_en', 'surname_en': 'фамилия_en', 'last_name_en': 'фамилия_en',
        'name': 'имя_en', 'name (en)': 'имя_en', 'name(en)': 'имя_en', 
        'name en': 'имя_en', 'name_en': 'имя_en', 'first_name_en': 'имя_en'
    }
    df.rename(columns=column_mapping, inplace=True)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID'."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # Кеш классов
    all_classes = SchoolClass.objects.select_related('school')
    classes_cache = {}
    for cls in all_classes:
        norm_name = normalize_cyrillic(cls.name).strip().upper()
        if norm_name not in classes_cache: classes_cache[norm_name] = []
        classes_cache[norm_name].append(cls)

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            raw_id = row.get('student_id')
            if pd.isna(raw_id): 
                skipped_count += 1
                continue
            
            student_id = str(raw_id).strip()
            if student_id.lower() in ['nan', 'none', '', '0']:
                skipped_count += 1
                continue

            if student_id.endswith('.0'): student_id = student_id[:-2]
            if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
                student_id = '0' + student_id
            
            if not student_id:
                skipped_count += 1
                continue

            try:
                student = Student.objects.get(student_id=student_id)
                student_exists = True
            except Student.DoesNotExist:
                student = None
                student_exists = False

            update_fields = {}
            def get_val(key):
                val = str(row.get(key, '')).strip()
                return val if val else None

            tj_last = get_val('фамилия_tj')
            if tj_last: update_fields['last_name_tj'] = tj_last
            tj_first = get_val('имя_tj')
            if tj_first: update_fields['first_name_tj'] = tj_first
            en_last = get_val('фамилия_en')
            if en_last: update_fields['last_name_en'] = en_last
            en_first = get_val('имя_en')
            if en_first: update_fields['first_name_en'] = en_first
            ru_last = get_val('фамилия_рус')
            if ru_last: update_fields['last_name_ru'] = normalize_cyrillic(ru_last)
            ru_first = get_val('имя_рус')
            if ru_first: update_fields['first_name_ru'] = normalize_cyrillic(ru_first)

            # Обновление существующего
            if student_exists:
                changed = False
                for field, value in update_fields.items():
                    if getattr(student, field) != value:
                        setattr(student, field, value)
                        changed = True
                
                class_name_raw = get_val('класс')
                if class_name_raw:
                    class_name_norm = normalize_cyrillic(class_name_raw).upper()
                    found_classes = classes_cache.get(class_name_norm)
                    if found_classes and len(found_classes) == 1:
                        if student.school_class != found_classes[0]:
                             student.school_class = found_classes[0]
                             changed = True
                
                if changed:
                    student.save()
                    updated_count += 1

            # Создание нового
            else:
                class_name_raw = get_val('класс')
                if not class_name_raw:
                     errors.append(f"Строка {row_num}: ID {student_id} не найден. Для создания нужен Класс.")
                     continue
                
                if not ru_last or not ru_first:
                     ru_last = update_fields.get('last_name_en') or update_fields.get('last_name_tj') or 'Unknown'
                     ru_first = update_fields.get('first_name_en') or update_fields.get('first_name_tj') or 'Unknown'

                class_name_norm = normalize_cyrillic(class_name_raw).upper()
                found_classes = classes_cache.get(class_name_norm)
                target_class = found_classes[0] if found_classes else None
                
                if not target_class:
                     errors.append(f"Строка {row_num}: Класс '{class_name_raw}' не найден в базе.")
                     continue

                new_student = Student(
                    student_id=student_id,
                    school_class=target_class,
                    status='ACTIVE',
                    last_name_ru=ru_last,
                    first_name_ru=ru_first,
                    last_name_tj=update_fields.get('last_name_tj', ''),
                    first_name_tj=update_fields.get('first_name_tj', ''),
                    last_name_en=update_fields.get('last_name_en', ''),
                    first_name_en=update_fields.get('first_name_en', '')
                )
                new_student.save()
                created_count += 1

    return {
        "created": created_count, 
        "updated": updated_count, 
        "skipped": skipped_count, 
        "errors": errors
    }


# =============================================================================
# --- 2. АНАЛИЗ РЕЗУЛЬТАТОВ (ШАГ 1: ПОИСК КОНФЛИКТОВ) ---
# =============================================================================

def analyze_student_results(excel_file):
    """
    Читает файл, сохраняет его во временное хранилище и ищет различия в ФИО.
    Возвращает список конфликтов и путь к файлу.
    """
    # 1. Сохраняем файл временно
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    temp_file_name = f"temp_gat_upload_{timestamp}.xlsx"
    # Сохраняем в папку temp/ внутри медиа
    file_path = default_storage.save(f"temp/{temp_file_name}", excel_file)
    
    try:
        # Читаем по полному пути
        full_path = default_storage.path(file_path)
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        return {'error': f"Ошибка чтения файла: {e}"}

    df.columns = [normalize_header(col) for col in df.columns]
    
    column_mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'рамз': 'student_id', 'id ученика': 'student_id',
        
        'surname': 'last_name', 'фамилия': 'last_name', 'насаб': 'last_name', 
        'lastname': 'last_name', 'surname_en': 'last_name',
        
        'name': 'first_name', 'имя': 'first_name', 'ном': 'first_name', 
        'firstname': 'first_name', 'name_en': 'first_name',
    }
    df.rename(columns=column_mapping, inplace=True)

    conflicts = []
    new_students_count = 0

    for index, row in df.iterrows():
        raw_id = row.get('student_id')
        if pd.isna(raw_id): continue
        student_id = str(raw_id).strip()
        if student_id.endswith('.0'): student_id = student_id[:-2]
        if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
            student_id = '0' + student_id
        if not student_id: continue

        student = Student.objects.filter(student_id=student_id).first()

        # Получаем ФИО из Excel
        excel_last = normalize_cyrillic(str(row.get('last_name', '')).strip())
        excel_first = normalize_cyrillic(str(row.get('first_name', '')).strip())
        if excel_last.lower() == 'nan': excel_last = ''
        if excel_first.lower() == 'nan': excel_first = ''
        
        if not student:
            # Это новый студент, конфликта нет (просто создадим его позже)
            new_students_count += 1
        else:
            # Студент есть, проверяем ФИО
            if excel_last and excel_first:
                # Нормализуем текущие данные из БД для корректного сравнения
                db_last = normalize_cyrillic(student.last_name_ru)
                db_first = normalize_cyrillic(student.first_name_ru)
                
                diff_last = db_last != excel_last
                diff_first = db_first != excel_first
                
                if diff_last or diff_first:
                    conflicts.append({
                        'student_id': student_id,
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
# --- 3. ЗАГРУЗКА РЕЗУЛЬТАТОВ (ШАГ 2: СОХРАНЕНИЕ) ---
# =============================================================================

def process_student_results_upload(gat_test, excel_file_path, overrides_map=None):
    """
    Обрабатывает загрузку результатов GAT.
    Принимает ПУТЬ к временному файлу (excel_file_path).
    overrides_map: {'ID_студента': 'excel'} - если нужно обновить имя.
    """
    if overrides_map is None:
        overrides_map = {}

    try:
        full_path = default_storage.path(excel_file_path)
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        return False, {'errors': [f"Ошибка чтения файла по пути {excel_file_path}: {e}"]}

    # Нормализация (такая же, как везде)
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

    created_students = 0
    updated_names = 0
    results_processed = 0
    errors = []

    # Мапа предметов
    subjects_map = {}
    for s in gat_test.subjects.all():
        subjects_map[normalize_cyrillic(s.name.strip().lower())] = s
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2 
            
            raw_id = row.get('student_id')
            if pd.isna(raw_id): continue
            student_id = str(raw_id).strip()
            if student_id.lower() in ['nan', 'none', '', '0']: continue
            if student_id.endswith('.0'): student_id = student_id[:-2]
            if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
                student_id = '0' + student_id
            if not student_id: continue
            
            student = Student.objects.filter(student_id=student_id).first()

            # Данные из Excel
            excel_last = normalize_cyrillic(str(row.get('last_name', '')).strip())
            excel_first = normalize_cyrillic(str(row.get('first_name', '')).strip())
            class_name_raw = str(row.get('class_name', '')).strip()

            if excel_last.lower() == 'nan': excel_last = ''
            if excel_first.lower() == 'nan': excel_first = ''
            if class_name_raw.lower() == 'nan': class_name_raw = ''

            # === ЛОГИКА ОБНОВЛЕНИЯ ИМЕНИ (ЕСЛИ ПОЛЬЗОВАТЕЛЬ РАЗРЕШИЛ) ===
            if student:
                # Смотрим, что решил пользователь ('db' или 'excel')
                decision = overrides_map.get(student_id, 'db')
                
                if decision == 'excel' and excel_last and excel_first:
                    # Обновляем, если есть разница
                    if student.last_name_ru != excel_last or student.first_name_ru != excel_first:
                        student.last_name_ru = excel_last
                        student.first_name_ru = excel_first
                        student.save()
                        updated_names += 1
            
            # === СОЗДАНИЕ НОВОГО (ЕСЛИ НЕТ В БАЗЕ) ===
            if not student:
                if not (excel_last and excel_first and class_name_raw):
                    errors.append(f"Строка {row_num}: Студент {student_id} не найден и нет данных для создания.")
                    continue 

                parent_class_name = gat_test.school_class.name
                if class_name_raw.startswith(parent_class_name):
                     full_class_name = class_name_raw 
                else:
                     full_class_name = f"{parent_class_name}{class_name_raw}" 
                full_class_name = normalize_cyrillic(full_class_name)

                school_class = SchoolClass.objects.filter(
                    school=gat_test.school, name=full_class_name, parent=gat_test.school_class
                ).first()

                if not school_class:
                    school_class = SchoolClass.objects.create(
                        school=gat_test.school, name=full_class_name, parent=gat_test.school_class
                    )

                student = Student.objects.create(
                    student_id=student_id,
                    school_class=school_class,
                    last_name_ru=excel_last,
                    first_name_ru=excel_first,
                    status='ACTIVE'
                )
                created_students += 1

            if student is None: continue

            # === ПОДСЧЕТ БАЛЛОВ ===
            scores_by_subject = {} 
            total_score = 0
            
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

                if is_correct: total_score += 1
                if str(subject.id) not in scores_by_subject: scores_by_subject[str(subject.id)] = {}
                scores_by_subject[str(subject.id)][str(q_num)] = is_correct

            StudentResult.objects.update_or_create(
                student=student,
                gat_test=gat_test,
                defaults={'total_score': total_score, 'scores_by_subject': scores_by_subject}
            )
            results_processed += 1

    # Удаляем временный файл в конце обработки
    if default_storage.exists(excel_file_path):
        default_storage.delete(excel_file_path)

    return True, {
        'total_unique_students': results_processed,
        'created_students': created_students,
        'updated_names': updated_names,
        'errors': errors
    }