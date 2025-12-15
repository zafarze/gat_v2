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

# =============================================================================
# --- ЗАГРУЗКА УЧЕНИКОВ (ОБНОВЛЕННАЯ ВЕРСИЯ) ---
# =============================================================================

def process_student_upload(excel_file):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    
    Логика:
    1. Ищем ученика по ID.
    2. Если найден -> ОБНОВЛЯЕМ имена (TJ, EN, RU) и класс (если указан).
    3. Если не найден -> СОЗДАЕМ нового (требуются ID, Класс, ФИО рус).
    """
    try:
        # Читаем как строки (dtype=str), чтобы ID и названия классов не превращались в числа
        df = pd.read_excel(excel_file, dtype=str)
        # Приводим заголовки к нижнему регистру и убираем пробелы
        df.columns = [str(col).strip().lower() for col in df.columns]
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    # --- 1. МАППИНГ КОЛОНОК (Словарь синонимов) ---
    column_mapping = {
        # ID
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id', 'id ученика': 'student_id',
        # Класс
        'section': 'класс', 'class': 'класс', 'класс': 'класс', 'class name': 'класс',
        
        # Русский (Базовый)
        'surname': 'фамилия_рус', 'lastname': 'фамилия_рус', 'фамилия': 'фамилия_рус', 'фамилия (ru)': 'фамилия_рус', 'фамилия (рус)': 'фамилия_рус',
        'name': 'имя_рус', 'firstname': 'имя_рус', 'имя': 'имя_рус', 'имя (ru)': 'имя_рус', 'имя (рус)': 'имя_рус',
        
        # Таджикский
        'насаб': 'фамилия_tj', 'nasab': 'фамилия_tj', 'насаб (tj)': 'фамилия_tj',
        'ном': 'имя_tj', 'nom': 'имя_tj', 'ном (tj)': 'имя_tj',
        
        # Английский
        'surname (en)': 'фамилия_en', 'surname_en': 'фамилия_en', 'last_name_en': 'фамилия_en',
        'name (en)': 'имя_en', 'name_en': 'имя_en', 'first_name_en': 'имя_en'
    }
    
    # Переименовываем колонки в DataFrame
    df.rename(columns=column_mapping, inplace=True)
    
    # Убираем дубликаты колонок (если вдруг были) и заполняем пустые значения
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    # --- 2. ПРОВЕРКА ОБЯЗАТЕЛЬНОГО ПОЛЯ ID ---
    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID' (или 'code', 'student_id'). Без неё обновление невозможно."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # --- 3. ПОДГОТОВКА КЕША КЛАССОВ ---
    # Загружаем все классы из БД, чтобы не делать запрос на каждой строке.
    # Ключом будет "нормализованное" имя (например, '10А' кириллицей), чтобы найти '10A' (латиницей).
    all_classes = SchoolClass.objects.select_related('school')
    classes_cache = {}
    
    for cls in all_classes:
        # Нормализуем имя ИЗ БАЗЫ (на случай если там записано латиницей)
        norm_name = normalize_cyrillic(cls.name).strip().upper()
        # Сохраняем в кеш. Ключ = Имя класса.
        # Если имена повторяются в разных школах, это упрощенный кеш.
        # В идеале лучше искать по школе + классу, но при массовой загрузке часто дают просто список.
        # Здесь мы предполагаем, что если имя класса уникально в рамках контекста загрузки.
        # Если у вас 5 школ и везде "5А", скрипт найдет первый попавшийся.
        # *Для точности лучше загружать учеников внутри конкретной школы.*
        if norm_name not in classes_cache:
             classes_cache[norm_name] = []
        classes_cache[norm_name].append(cls)

    # --- 4. ОБРАБОТКА СТРОК ---
    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2 # Номер строки в Excel (учитывая заголовок)
            
            # Получаем ID
            student_id = str(row.get('student_id')).strip()
            if student_id.endswith('.0'):
                student_id = student_id[:-2]
                
            # Исправление: ВОССТАНАВЛИВАЕМ ВЕДУЩИЙ НОЛЬ
            # Вы сказали, что каждый ID должен начинаться с 0.
            # Если программа видит "30586", она превратит его в "030586"
            if student_id and not student_id.startswith('0'):
                student_id = '0' + student_idN
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
            
            # Функция-помощник для безопасного получения строки
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

            # Русские (тоже можно обновить/исправить)
            ru_last = get_val('фамилия_рус')
            if ru_last: update_fields['last_name_ru'] = normalize_cyrillic(ru_last)
            
            ru_first = get_val('имя_рус')
            if ru_first: update_fields['first_name_ru'] = normalize_cyrillic(ru_first)

            # --- СЦЕНАРИЙ А: УЧЕНИК СУЩЕСТВУЕТ (ОБНОВЛЕНИЕ) ---
            if student_exists:
                # 1. Обновляем имена
                for field, value in update_fields.items():
                    setattr(student, field, value)
                
                # 2. Обновляем класс (если указан)
                class_name_raw = get_val('класс')
                if class_name_raw:
                    class_name_norm = normalize_cyrillic(class_name_raw).upper()
                    found_classes = classes_cache.get(class_name_norm)
                    
                    if found_classes:
                        # Если нашли ровно один класс с таким именем - привязываем
                        if len(found_classes) == 1:
                            student.school_class = found_classes[0]
                        else:
                            # Если нашли несколько (например "5А" в Школе 1 и "5А" в Школе 2),
                            # то лучше не менять класс, чтобы не перекинуть в другую школу.
                            # Если текущий класс студента уже совпадает по имени, оставляем.
                            pass 
                    else:
                        # Класс указан, но не найден в базе
                        pass # Просто игнорируем смену класса, обновляем только имена

                student.save()
                updated_count += 1

            # --- СЦЕНАРИЙ Б: СОЗДАНИЕ НОВОГО (НУЖНЫ ID, КЛАСС, РУС. ИМЕНА) ---
            else:
                class_name_raw = get_val('класс')
                
                # Проверка обязательных полей для создания
                if not class_name_raw or not ru_last or not ru_first:
                    errors.append(f"Строка {row_num}: ID {student_id} не найден. Для создания нужны: Класс, Фамилия (рус), Имя (рус).")
                    continue
                
                # Поиск класса
                class_name_norm = normalize_cyrillic(class_name_raw).upper()
                found_classes = classes_cache.get(class_name_norm)
                
                target_class = None
                if found_classes:
                    if len(found_classes) == 1:
                        target_class = found_classes[0]
                    else:
                        # Если классов несколько, пробуем найти "похожий" (сложная логика, здесь берем первый или ошибку)
                        # В идеале нужно передавать ID школы при загрузке.
                        # Здесь берем первый, предполагая, что вы загружаете уникальные имена или работаете в контексте одной школы
                        target_class = found_classes[0] 
                
                if not target_class:
                     errors.append(f"Строка {row_num}: Класс '{class_name_raw}' не найден в базе.")
                     continue

                # Создаем
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
# --- ЗАГРУЗКА РЕЗУЛЬТАТОВ (БЕЗ ИЗМЕНЕНИЙ) ---
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
    # Добавляем маппинг по сокращениям (например "МАТ" -> Математика)
    for s in gat_test.subjects.all():
        if s.abbreviation:
            subjects_map[normalize_cyrillic(s.abbreviation.strip().lower())] = s

    # Кеширование вопросов
    questions_cache = {} # { subject_id: { question_number: QuestionObj } }
    for q in Question.objects.filter(gat_test=gat_test):
        if q.subject_id not in questions_cache:
            questions_cache[q.subject_id] = {}
        questions_cache[q.subject_id][q.question_number] = q

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            student_id = str(row.get('Code', '')).strip()
            
            if not student_id or student_id.lower() == 'nan':
                continue

            # 1. Поиск или создание студента
            student = Student.objects.filter(student_id=student_id).first()

            if not student:
                # Если студента нет - пытаемся создать
                last_name = normalize_cyrillic(str(row.get('Surname', '')).strip())
                first_name = normalize_cyrillic(str(row.get('Name', '')).strip())
                class_name_raw = str(row.get('Section', '')).strip()

                if not (last_name and first_name and class_name_raw):
                    errors.append(f"Строка {row_num}: Не найден студент {student_id} и не хватает данных для создания.")
                    continue

                # Логика создания классов (10 -> 10А)
                parent_class_name = gat_test.school_class.name # Например "10"
                
                # Если в Excel "А" или "10А"
                if class_name_raw.startswith(parent_class_name):
                     full_class_name = class_name_raw # Уже "10А"
                else:
                     full_class_name = f"{parent_class_name}{class_name_raw}" # "10" + "А"

                full_class_name = normalize_cyrillic(full_class_name)

                # Ищем подкласс
                school_class = SchoolClass.objects.filter(
                    school=gat_test.school, 
                    name=full_class_name,
                    parent=gat_test.school_class
                ).first()

                if not school_class:
                    # Создаем подкласс (10А), если его нет
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

            # 2. Обработка результатов
            scores_by_subject = {} # { subject_id: { q_num: bool } }
            total_score = 0
            
            # Пробегаемся по колонкам (МАТ_1, МАТ_2...)
            for col_name in df.columns:
                if '_' not in col_name:
                    continue
                
                parts = col_name.rsplit('_', 1) # ['МАТ', '1']
                if len(parts) != 2:
                    continue
                
                subj_name_raw, q_num_str = parts
                subj_name_norm = normalize_cyrillic(subj_name_raw.lower())
                
                if subj_name_norm not in subjects_map:
                    continue
                    
                if not q_num_str.isdigit():
                    continue

                subject = subjects_map[subj_name_norm]
                q_num = int(q_num_str)
                
                # Получаем балл (1 или 0)
                try:
                    val = int(row[col_name])
                    is_correct = (val > 0)
                except (ValueError, TypeError):
                    is_correct = False

                if is_correct:
                    total_score += 1

                # Записываем в структуру
                if str(subject.id) not in scores_by_subject:
                    scores_by_subject[str(subject.id)] = {}
                
                scores_by_subject[str(subject.id)][str(q_num)] = is_correct

            # Сохраняем результат
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