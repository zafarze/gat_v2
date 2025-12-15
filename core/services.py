# D:\New_GAT\core\services.py

import pandas as pd
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from .models import Student, StudentResult, GatTest, Question, SchoolClass, Subject

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

def normalize_cyrillic(text):
    """
    Заменяет латинские буквы, похожие на кириллицу, на их кириллические аналоги.
    Также убирает лишние пробелы.
    """
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    
    mapping = {
        'A': 'А', 'a': 'а',
        'B': 'В', 'b': 'в', # Внимание: Латинская B -> Русская В
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
    Специальная очистка для заголовков Excel.
    Приводит к нижнему регистру, убирает лишние пробелы.
    Если в заголовке есть 'name' или 'surname', пытается убрать кириллицу (обратная операция).
    """
    if not isinstance(text, str):
        return str(text)
    
    text = text.lower().strip()
    # Заменяем кириллические буквы на латинские в ключевых словах, 
    # чтобы 'Nаme' (с русской а) стало 'name'
    mapping_to_latin = {
        'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'у': 'y'
    }
    res = []
    for char in text:
        res.append(mapping_to_latin.get(char, char))
    return "".join(res)

# =============================================================================
# --- ЗАГРУЗКА УЧЕНИКОВ (ОБНОВЛЕННАЯ ВЕРСИЯ) ---
# =============================================================================

def process_student_upload(excel_file):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    """
    try:
        # Читаем как строки (dtype=str)
        df = pd.read_excel(excel_file, dtype=str)
        
        # ✨ УЛУЧШЕНИЕ: Применяем усиленную очистку заголовков
        df.columns = [normalize_header(col) for col in df.columns]
        
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    # --- 1. МАППИНГ КОЛОНОК (Словарь синонимов) ---
    column_mapping = {
        # ID
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 'id ученика': 'student_id',
        # Класс
        'section': 'класс', 'class': 'класс', 'класс': 'класс', 'class name': 'класс',
        
        # Русский (Базовый)
        'surname': 'фамилия_рус', 'lastname': 'фамилия_рус', 'фамилия': 'фамилия_рус', 
        'фамилия (ru)': 'фамилия_рус', 'фамилия (рус)': 'фамилия_рус',
        'name': 'имя_рус', 'firstname': 'имя_рус', 'имя': 'имя_рус', 
        'имя (ru)': 'имя_рус', 'имя (рус)': 'имя_рус',
        
        # Таджикский
        'насаб': 'фамилия_tj', 'nasab': 'фамилия_tj', 'насаб (tj)': 'фамилия_tj',
        'ном': 'имя_tj', 'nom': 'имя_tj', 'ном (tj)': 'имя_tj',
        
        # Английский (Добавлено больше вариантов)
        'surname (en)': 'фамилия_en', 'surname(en)': 'фамилия_en', 'surname en': 'фамилия_en',
        'surname_en': 'фамилия_en', 'last_name_en': 'фамилия_en', 'eng surname': 'фамилия_en',
        
        'name (en)': 'имя_en', 'name(en)': 'имя_en', 'name en': 'имя_en',
        'name_en': 'имя_en', 'first_name_en': 'имя_en', 'eng name': 'имя_en'
    }
    
    # Переименовываем колонки в DataFrame
    df.rename(columns=column_mapping, inplace=True)
    
    # Убираем дубликаты колонок и заполняем пустоты
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    # --- 2. ПРОВЕРКА ОБЯЗАТЕЛЬНОГО ПОЛЯ ID ---
    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID' (или 'code', 'student_id')."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # --- 3. ПОДГОТОВКА КЕША КЛАССОВ ---
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
            
            # Получаем ID
            student_id = str(row.get('student_id')).strip()
            if student_id.endswith('.0'):
                student_id = student_id[:-2]
                
            # ВОССТАНАВЛИВАЕМ ВЕДУЩИЙ НОЛЬ (Исправлено)
            if student_id and not student_id.startswith('0'):
                student_id = '0' + student_id
            
            if not student_id:
                skipped_count += 1
                continue

            # Ищем ученика
            try:
                student = Student.objects.get(student_id=student_id)
                student_exists = True
            except Student.DoesNotExist:
                student = None
                student_exists = False

            # Сбор данных для обновления
            update_fields = {}
            
            def get_val(key):
                val = str(row.get(key, '')).strip()
                return val if val else None

            # Таджикские
            tj_last = get_val('фамилия_tj')
            if tj_last: update_fields['last_name_tj'] = tj_last
            
            tj_first = get_val('имя_tj')
            if tj_first: update_fields['first_name_tj'] = tj_first

            # Английские (Вот здесь данные должны подхватиться благодаря новому маппингу)
            en_last = get_val('фамилия_en')
            if en_last: update_fields['last_name_en'] = en_last
            
            en_first = get_val('имя_en')
            if en_first: update_fields['first_name_en'] = en_first

            # Русские
            ru_last = get_val('фамилия_рус')
            if ru_last: update_fields['last_name_ru'] = normalize_cyrillic(ru_last)
            
            ru_first = get_val('имя_рус')
            if ru_first: update_fields['first_name_ru'] = normalize_cyrillic(ru_first)

            # --- СЦЕНАРИЙ А: ОБНОВЛЕНИЕ ---
            if student_exists:
                # 1. Обновляем имена (включая английские, если они есть в файле)
                for field, value in update_fields.items():
                    setattr(student, field, value)
                
                # 2. Обновляем класс (если указан)
                class_name_raw = get_val('класс')
                if class_name_raw:
                    class_name_norm = normalize_cyrillic(class_name_raw).upper()
                    found_classes = classes_cache.get(class_name_norm)
                    if found_classes:
                        if len(found_classes) == 1:
                            student.school_class = found_classes[0]
                        # Иначе оставляем старый, чтобы не перепутать школу

                student.save()
                updated_count += 1

            # --- СЦЕНАРИЙ Б: СОЗДАНИЕ ---
            else:
                class_name_raw = get_val('класс')
                if not class_name_raw or not ru_last or not ru_first:
                    errors.append(f"Строка {row_num}: ID {student_id} не найден. Для создания нужны: Класс, Фамилия (рус), Имя (рус).")
                    continue
                
                class_name_norm = normalize_cyrillic(class_name_raw).upper()
                found_classes = classes_cache.get(class_name_norm)
                
                target_class = None
                if found_classes:
                    target_class = found_classes[0] # Берем первый найденный
                
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
# --- ЗАГРУЗКА РЕЗУЛЬТАТОВ (ОСТАЕТСЯ БЕЗ ИЗМЕНЕНИЙ) ---
# =============================================================================

def process_student_results_upload(gat_test, excel_file):
    """
    Обрабатывает загрузку результатов GAT.
    """
    try:
        df = pd.read_excel(excel_file, dtype={'Code': str})
    except Exception as e:
        return False, {'errors': [f"Ошибка чтения файла: {e}"]}

    df.columns = [str(col).strip() for col in df.columns]

    created_students = 0
    results_processed = 0
    errors = []

    # Кеширование предметов
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
                skipped_count += 1
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