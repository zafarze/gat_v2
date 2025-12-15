# D:\New_GAT\core\services.py

import pandas as pd
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from .models import Student, StudentResult, GatTest, Question, SchoolClass, Subject
from datetime import datetime
import re

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

def normalize_cyrillic(text):
    """
    Заменяет латинские буквы, похожие на кириллицу, на их кириллические аналоги.
    Используется для САМИХ ДАННЫХ (имен), чтобы 'A' (lat) стало 'А' (cyr).
    """
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    
    mapping = {
        'A': 'А', 'a': 'а',
        'B': 'В', 'b': 'в', 
        'E': 'Е', 'e': 'е',
        'K': 'К', 'k': 'к',
        'M': 'М', 'm': 'м',
        'H': 'Н', 'h': 'н',
        'O': 'О', 'o': 'о',
        'P': 'Р', 'p': 'р',
        'C': 'С', 'c': 'с',
        'T': 'Т', 't': 'т',
        'X': 'Х', 'x': 'х',
        'y': 'у', 'Y': 'У'
    }
    
    result = []
    for char in text:
        result.append(mapping.get(char, char))
    
    return "".join(result).strip()

def normalize_header(text):
    """
    ✨ ИСПРАВЛЕНО: Упрощенная очистка для заголовков Excel.
    Теперь только нижний регистр и удаление пробелов.
    Больше НЕ заменяет буквы, чтобы не ломать "Ном" и "Насаб".
    """
    if not isinstance(text, str):
        return str(text)
    
    return text.lower().strip()

# =============================================================================
# --- ЗАГРУЗКА УЧЕНИКОВ ---
# =============================================================================

def process_student_upload(excel_file):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    """
    try:
        # Читаем как строки (dtype=str)
        df = pd.read_excel(excel_file, dtype=str)
        # Применяем исправленную функцию очистки
        df.columns = [normalize_header(col) for col in df.columns]
        
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    # --- 1. МАППИНГ КОЛОНОК ---
    column_mapping = {
        # ID
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 'id ученика': 'student_id',
        # Класс
        'section': 'класс', 'class': 'класс', 'класс': 'класс', 'class name': 'класс', 'grade': 'класс',
        
        # Русский (Base)
        'lastname': 'фамилия_рус', 'фамилия': 'фамилия_рус', 
        'фамилия (ru)': 'фамилия_рус', 'фамилия (рус)': 'фамилия_рус',
        
        'firstname': 'имя_рус', 'имя': 'имя_рус', 
        'имя (ru)': 'имя_рус', 'имя (рус)': 'имя_рус',
        
        # Таджикский
        'насаб': 'фамилия_tj', 'nasab': 'фамилия_tj', 'насаб (tj)': 'фамилия_tj',
        'ном': 'имя_tj', 'nom': 'имя_tj', 'ном (tj)': 'имя_tj',
        
        # Английский
        'surname': 'фамилия_en', 'surname (en)': 'фамилия_en', 'surname(en)': 'фамилия_en', 
        'surname en': 'фамилия_en', 'surname_en': 'фамилия_en', 'last_name_en': 'фамилия_en',
        
        'name': 'имя_en', 'name (en)': 'имя_en', 'name(en)': 'имя_en', 
        'name en': 'имя_en', 'name_en': 'имя_en', 'first_name_en': 'имя_en'
    }
    
    # Переименовываем колонки
    df.rename(columns=column_mapping, inplace=True)
    
    # Чистим
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    # --- 2. ПРОВЕРКА ID ---
    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID' (или 'code', 'student_id')."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # --- 3. КЕШ КЛАССОВ ---
    all_classes = SchoolClass.objects.select_related('school')
    classes_cache = {}
    
    for cls in all_classes:
        norm_name = normalize_cyrillic(cls.name).strip().upper()
        if norm_name not in classes_cache:
             classes_cache[norm_name] = []
        classes_cache[norm_name].append(cls)

    # --- 4. ОБРАБОТКА СТРОК ---
    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            
            # ID
            student_id = str(row.get('student_id')).strip()
            if student_id.endswith('.0'):
                student_id = student_id[:-2]
            if student_id and not student_id.startswith('0') and len(student_id) < 6:
                student_id = '0' + student_id
            
            if not student_id:
                skipped_count += 1
                continue

            # Поиск
            try:
                student = Student.objects.get(student_id=student_id)
                student_exists = True
            except Student.DoesNotExist:
                student = None
                student_exists = False

            # Сбор данных
            update_fields = {}
            def get_val(key):
                val = str(row.get(key, '')).strip()
                return val if val else None

            # Таджикские (Теперь должны работать!)
            tj_last = get_val('фамилия_tj')
            if tj_last: update_fields['last_name_tj'] = tj_last
            
            tj_first = get_val('имя_tj')
            if tj_first: update_fields['first_name_tj'] = tj_first

            # Английские
            en_last = get_val('фамилия_en')
            if en_last: update_fields['last_name_en'] = en_last
            
            en_first = get_val('имя_en')
            if en_first: update_fields['first_name_en'] = en_first

            # Русские
            ru_last = get_val('фамилия_рус')
            if ru_last: update_fields['last_name_ru'] = normalize_cyrillic(ru_last)
            
            ru_first = get_val('имя_рус')
            if ru_first: update_fields['first_name_ru'] = normalize_cyrillic(ru_first)

            # --- А: ОБНОВЛЕНИЕ ---
            if student_exists:
                changed = False
                for field, value in update_fields.items():
                    if getattr(student, field) != value:
                        setattr(student, field, value)
                        changed = True
                
                # Класс
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

            # --- Б: СОЗДАНИЕ ---
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
                
                target_class = None
                if found_classes:
                    target_class = found_classes[0]
                
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

# --- process_student_results_upload остается без изменений ---
def process_student_results_upload(gat_test, excel_file):
    try:
        df = pd.read_excel(excel_file, dtype={'Code': str})
    except Exception as e:
        return False, {'errors': [f"Ошибка чтения файла: {e}"]}

    df.columns = [str(col).strip() for col in df.columns]

    created_students = 0
    results_processed = 0
    errors = []

    subjects_map = {
        normalize_cyrillic(s.name.strip().lower()): s 
        for s in gat_test.subjects.all()
    }
    for s in gat_test.subjects.all():
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2 
            
            student_id = str(row.get('student_id')).strip()
            if student_id.endswith('.0'):
                student_id = student_id[:-2]
            if student_id and not student_id.startswith('0'):
                student_id = '0' + student_id
            
            if not student_id:
                continue

            student = Student.objects.filter(student_id=student_id).first()

            if not student:
                last_name = normalize_cyrillic(str(row.get('Surname', '')).strip())
                first_name = normalize_cyrillic(str(row.get('Name', '')).strip())
                class_name_raw = str(row.get('Section', '')).strip()

                if not (last_name and first_name and class_name_raw):
                    errors.append(f"Строка {row_num}: Не найден студент {student_id} и не хватает данных для создания.")
                    continue

                parent_class_name = gat_test.school_class.name
                if class_name_raw.startswith(parent_class_name):
                     full_class_name = class_name_raw 
                else:
                     full_class_name = f"{parent_class_name}{class_name_raw}" 

                full_class_name = normalize_cyrillic(full_class_name)

                school_class = SchoolClass.objects.filter(
                    school=gat_test.school, 
                    name=full_class_name,
                    parent=gat_test.school_class
                ).first()

                if not school_class:
                    school_class = SchoolClass.objects.create(
                        school=gat_test.school,
                        name=full_class_name,
                        parent=gat_test.school_class
                    )

                student = Student.objects.create(
                    student_id=student_id,
                    school_class=school_class,
                    last_name_ru=last_name,
                    first_name_ru=first_name,
                    status='ACTIVE'
                )
                created_students += 1

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
                    val = int(row[col_name])
                    is_correct = (val > 0)
                except (ValueError, TypeError):
                    is_correct = False

                if is_correct:
                    total_score += 1

                if str(subject.id) not in scores_by_subject:
                    scores_by_subject[str(subject.id)] = {}
                
                scores_by_subject[str(subject.id)][str(q_num)] = is_correct

            StudentResult.objects.update_or_create(
                student=student,
                gat_test=gat_test,
                defaults={
                    'total_score': total_score,
                    'scores_by_subject': scores_by_subject
                }
            )
            results_processed += 1

    return True, {
        'total_unique_students': results_processed,
        'created_students': created_students,
        'errors': errors
    }

def extract_test_date_from_excel(uploaded_file):
    """
    Пытается извлечь дату теста из имени файла.
    Поддерживаемые форматы: YYYY-MM-DD, YYYY_MM_DD, DD-MM-YYYY, DD.MM.YYYY
    """
    filename = uploaded_file.name
    
    # Шаблоны дат
    date_patterns = [
        r'(\d{4})[-_](\d{2})[-_](\d{2})',  # 2025-12-15 или 2025_12_15
        r'(\d{2})[-_\.](\d{2})[-_\.](\d{4})' # 15-12-2025 или 15.12.2025
    ]

    for pattern in date_patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4: # YYYY-MM-DD
                    return datetime.strptime(f"{groups[0]}-{groups[1]}-{groups[2]}", "%Y-%m-%d").date()
                else: # DD-MM-YYYY
                    return datetime.strptime(f"{groups[2]}-{groups[1]}-{groups[0]}", "%Y-%m-%d").date()
            except ValueError:
                continue
                
    return None