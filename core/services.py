# D:\New_GAT\core\services.py

import pandas as pd
import re
from datetime import datetime
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from .models import Student, StudentResult, GatTest, Question, SchoolClass, Subject

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

def normalize_cyrillic(text):
    """
    Заменяет латинские буквы, похожие на кириллицу, на их кириллические аналоги.
    Используется для нормализации имен (чтобы A (eng) стало А (rus)).
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
    Очистка заголовков Excel: нижний регистр и удаление пробелов.
    """
    if not isinstance(text, str):
        return str(text)
    
    return text.lower().strip()

def extract_test_date_from_excel(uploaded_file):
    """
    Пытается извлечь дату теста из имени файла.
    Поддерживаемые форматы: YYYY-MM-DD, YYYY_MM_DD, DD-MM-YYYY, DD.MM.YYYY
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
                if len(groups[0]) == 4: # YYYY-MM-DD
                    return datetime.strptime(f"{groups[0]}-{groups[1]}-{groups[2]}", "%Y-%m-%d").date()
                else: # DD-MM-YYYY
                    return datetime.strptime(f"{groups[2]}-{groups[1]}-{groups[0]}", "%Y-%m-%d").date()
            except ValueError:
                continue
                
    return None

# =============================================================================
# --- ЗАГРУЗКА УЧЕНИКОВ (ПОЛНАЯ ВЕРСИЯ) ---
# =============================================================================

def process_student_upload(excel_file):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    """
    try:
        # Читаем как строки (dtype=str) чтобы сохранить ведущие нули в ID
        df = pd.read_excel(excel_file, dtype=str)
        # Нормализуем заголовки
        df.columns = [normalize_header(col) for col in df.columns]
        
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    # --- 1. МАППИНГ КОЛОНОК (3 ЯЗЫКА) ---
    column_mapping = {
        # ID / Код
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 
        'id ученика': 'student_id', 'код': 'student_id', 'рамз': 'student_id',
        
        # Класс / Синф
        'section': 'класс', 'class': 'класс', 'класс': 'класс', 
        'class name': 'класс', 'grade': 'класс', 'синф': 'класс',
        
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
    
    # Чистим от дублей колонок и NaN
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    # --- 2. ПРОВЕРКА ID ---
    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID' (или 'code', 'student_id', 'код', 'рамз')."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # --- 3. КЕШ КЛАССОВ (Оптимизация) ---
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
            
            # --- ЧТЕНИЕ ID ---
            raw_id = row.get('student_id')
            if pd.isna(raw_id) or raw_id is None:
                skipped_count += 1
                continue

            student_id = str(raw_id).strip()
            if student_id.lower() in ['nan', 'none', '', '0']:
                skipped_count += 1
                continue

            if student_id.endswith('.0'):
                student_id = student_id[:-2]
            
            if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
                student_id = '0' + student_id
            
            if not student_id:
                skipped_count += 1
                continue

            # Поиск студента
            try:
                student = Student.objects.get(student_id=student_id)
                student_exists = True
            except Student.DoesNotExist:
                student = None
                student_exists = False

            # Сбор полей для обновления
            update_fields = {}
            def get_val(key):
                val = str(row.get(key, '')).strip()
                return val if val else None

            # Таджикские
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

            # --- А: ОБНОВЛЕНИЕ СУЩЕСТВУЮЩЕГО ---
            if student_exists:
                changed = False
                for field, value in update_fields.items():
                    if getattr(student, field) != value:
                        setattr(student, field, value)
                        changed = True
                
                # Обновление класса
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

            # --- Б: СОЗДАНИЕ НОВОГО ---
            else:
                class_name_raw = get_val('класс')
                if not class_name_raw:
                     errors.append(f"Строка {row_num}: ID {student_id} не найден. Для создания нужен Класс.")
                     continue
                
                # Если нет русского имени, берем любое другое
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

# =============================================================================
# --- ЗАГРУЗКА РЕЗУЛЬТАТОВ (ПОЛНАЯ БОЕВАЯ ВЕРСИЯ) ---
# =============================================================================

def process_student_results_upload(gat_test, excel_file):
    """
    Обрабатывает загрузку результатов GAT.
    Включает маппинг колонок (RU/TJ/EN) и создание студентов 'на лету'.
    """
    try:
        # Читаем все как строки
        df = pd.read_excel(excel_file, dtype=str)
    except Exception as e:
        return False, {'errors': [f"Ошибка чтения файла: {e}"]}

    # 1. Нормализуем заголовки
    df.columns = [normalize_header(col) for col in df.columns]

    # 2. Маппинг колонок (Русский, Английский, Таджикский)
    column_mapping = {
        # ID / Код
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'рамз': 'student_id', 'id ученика': 'student_id',
        
        # Фамилия / Насаб / Surname
        'surname': 'last_name', 'фамилия': 'last_name', 'насаб': 'last_name', 
        'lastname': 'last_name', 'surname_en': 'last_name',
        
        # Имя / Ном / Name
        'name': 'first_name', 'имя': 'first_name', 'ном': 'first_name', 
        'firstname': 'first_name', 'name_en': 'first_name',
        
        # Класс / Синф / Class
        'section': 'class_name', 'class': 'class_name', 'класс': 'class_name', 
        'синф': 'class_name', 'grade': 'class_name'
    }
    df.rename(columns=column_mapping, inplace=True)

    created_students = 0
    results_processed = 0
    errors = []

    # Подготовка мапы предметов
    subjects_map = {}
    for s in gat_test.subjects.all():
        subjects_map[normalize_cyrillic(s.name.strip().lower())] = s
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2 
            
            # --- Чтение и очистка ID ---
            raw_id = row.get('student_id')
            if pd.isna(raw_id) or raw_id is None:
                continue

            student_id = str(raw_id).strip()
            if student_id.lower() in ['nan', 'none', '', '0']:
                continue

            if student_id.endswith('.0'):
                student_id = student_id[:-2]
                
            if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
                student_id = '0' + student_id
            
            if not student_id:
                continue
            
            # --- Поиск студента ---
            student = Student.objects.filter(student_id=student_id).first()

            # --- Если студент не найден, пробуем создать (только если есть данные) ---
            if not student:
                last_name = normalize_cyrillic(str(row.get('last_name', '')).strip())
                first_name = normalize_cyrillic(str(row.get('first_name', '')).strip())
                class_name_raw = str(row.get('class_name', '')).strip()

                # Убираем 'nan' строки
                if last_name.lower() == 'nan': last_name = ''
                if first_name.lower() == 'nan': first_name = ''
                if class_name_raw.lower() == 'nan': class_name_raw = ''

                # Если данных не хватает — пропускаем с записью ошибки, но НЕ падаем
                if not (last_name and first_name and class_name_raw):
                    errors.append(f"Строка {row_num}: Студент {student_id} не найден, и нет данных для создания (Фамилия, Имя, Класс).")
                    continue 

                # Логика определения класса (наследование от теста)
                parent_class_name = gat_test.school_class.name # Например "10"
                
                # Если в файле "10А", а тест для "10" -> все ок
                # Если в файле "А", а тест для "10" -> делаем "10А"
                if class_name_raw.startswith(parent_class_name):
                     full_class_name = class_name_raw 
                else:
                     full_class_name = f"{parent_class_name}{class_name_raw}" 
                
                full_class_name = normalize_cyrillic(full_class_name)

                # Ищем класс в базе
                school_class = SchoolClass.objects.filter(
                    school=gat_test.school, 
                    name=full_class_name,
                    parent=gat_test.school_class
                ).first()

                # Если класса нет - создаем
                if not school_class:
                    school_class = SchoolClass.objects.create(
                        school=gat_test.school,
                        name=full_class_name,
                        parent=gat_test.school_class
                    )

                # Создаем студента
                student = Student.objects.create(
                    student_id=student_id,
                    school_class=school_class,
                    last_name_ru=last_name,
                    first_name_ru=first_name,
                    status='ACTIVE'
                )
                created_students += 1

            # --- КРИТИЧЕСКАЯ ЗАЩИТА ---
            # Если student все еще None (создание не удалось), пропускаем
            if student is None:
                continue

            # --- Подсчет баллов ---
            scores_by_subject = {} 
            total_score = 0
            
            for col_name in df.columns:
                # Ищем колонки формата "Математика_1", "МАТ_5" и т.д.
                if '_' not in col_name: continue
                
                parts = col_name.rsplit('_', 1)
                if len(parts) != 2: continue
                
                subj_name_raw, q_num_str = parts
                subj_name_norm = normalize_cyrillic(subj_name_raw.lower())
                
                # Проверяем, есть ли такой предмет и номер вопроса - число
                if subj_name_norm not in subjects_map: continue
                if not q_num_str.isdigit(): continue

                subject = subjects_map[subj_name_norm]
                q_num = int(q_num_str)
                
                try:
                    # Конвертируем значение (1, 0, +, -) в булево
                    val_str = str(row[col_name]).replace(',', '.')
                    val = float(val_str)
                    is_correct = (val > 0)
                except (ValueError, TypeError):
                    is_correct = False

                if is_correct:
                    total_score += 1

                if str(subject.id) not in scores_by_subject:
                    scores_by_subject[str(subject.id)] = {}
                
                scores_by_subject[str(subject.id)][str(q_num)] = is_correct

            # --- Сохранение результата ---
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