# D:\Project Archive\GAT\core\services.py

import pandas as pd
import re
import os
import uuid
from collections import defaultdict
from datetime import datetime

from django.db import transaction, models, IntegrityError # ✨ Добавлен IntegrityError
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage

from .models import (
    Student, StudentResult, GatTest, Question, 
    SchoolClass, Subject, StudentAnswer
)
# Импортируем утилиту для расчета оценки
from .utils import calculate_grade_from_percentage 

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

def normalize_cyrillic(text):
    """
    Заменяет латинские буквы, похожие на кириллицу, на их кириллические аналоги.
    Помогает избежать дублей вида "10A" (eng) и "10А" (ru).
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
    Примеры: '2025-12-15_Result.xlsx', '15-12-2025.xlsx'
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
                # Определяем формат YMD или DMY
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
    
    update_names=True: (По умолчанию) Обновляет ФИО в базе, если они отличаются в Excel.
    update_names=False: Игнорирует отличия в ФИО, оставляя старые данные из базы.
    """
    # Извлекаем данные из строки
    raw_id = row_data.get('student_id')
    student_id = str(raw_id).strip() if pd.notna(raw_id) else None
    if student_id and student_id.endswith('.0'): student_id = student_id[:-2]
    if student_id and student_id.lower() in ['nan', 'none', '', '0']: student_id = None
    if student_id and student_id.isdigit() and len(student_id) < 6: student_id = student_id.zfill(6)

    # Очищаем имена от пробелов и нормализуем
    last_name = normalize_cyrillic(str(row_data.get('last_name', '')).strip()).title()
    first_name = normalize_cyrillic(str(row_data.get('first_name', '')).strip()).title()
    
    # Класс из Excel (если есть)
    excel_class_name = str(row_data.get('class_name', '')).strip()

    # 1. Сначала пробуем найти по ID (самый надежный способ)
    if student_id:
        student = Student.objects.filter(student_id=student_id).first()
        if student:
            # Если нашли по ID, проверяем, нужно ли обновлять данные
            updated = False
            
            # --- ✨ ВАЖНОЕ ИЗМЕНЕНИЕ: Обновляем только если разрешено ---
            if update_names:
                if last_name and (student.last_name_ru != last_name):
                    student.last_name_ru = last_name
                    updated = True
                if first_name and (student.first_name_ru != first_name):
                    student.first_name_ru = first_name
                    updated = True
                
                if updated:
                    student.save()
            
            return student, False, updated

    # 2. Если ID нет или не нашли — ищем по ФИО и Классу (Умный поиск)
    if last_name and first_name:
        # Собираем список классов, где будем искать
        classes_to_search = [test_school_class]
        
        # Если тест для Параллели (например, 7), добавляем все подклассы (7А, 7Б...)
        if test_school_class.parent is None:
            subclasses = test_school_class.subclasses.all()
            classes_to_search.extend(subclasses)

        # Ищем совпадение по Имени + Фамилии + (Класс ИЛИ его подклассы)
        student = Student.objects.filter(
            school_class__in=classes_to_search,
            last_name_ru__iexact=last_name,
            first_name_ru__iexact=first_name
        ).first()

        if student:
            # Нашли! Обновляем ID если в базе его не было, а в файле пришел
            updated = False
            if student_id and student.student_id != student_id:
                student.student_id = student_id
                updated = True
                student.save()
            return student, False, updated

    # 3. Если совсем не нашли — создаем нового
    # Формируем ID, если его нет
    final_id = student_id if student_id else f"TEMP-{uuid.uuid4().hex[:8].upper()}"
    
    # --- ЛОГИКА ОПРЕДЕЛЕНИЯ КЛАССА ---
    target_class = test_school_class
    
    if excel_class_name:
        norm_excel_class = normalize_cyrillic(excel_class_name).upper() # Например "Б" или "6Б"
        
        # Вариант А: В Excel написано полное имя (например, "6А")
        found_class = SchoolClass.objects.filter(
            school=test_school, 
            name__iexact=norm_excel_class,
            parent=test_school_class 
        ).first()
        
        # Вариант Б: В Excel написана только БУКВА (например, "Б"), а тест для параллели "6"
        if not found_class and test_school_class.parent is None:
            # Пытаемся склеить: "6" + "Б" -> "6Б"
            combined_name = f"{test_school_class.name}{norm_excel_class}"
            found_class = SchoolClass.objects.filter(
                school=test_school,
                name__iexact=combined_name,
                parent=test_school_class
            ).first()

        # Вариант В: Глобальный поиск по школе
        if not found_class:
             found_class = SchoolClass.objects.filter(school=test_school, name__iexact=norm_excel_class).first()

        if found_class:
            target_class = found_class

    # --- ✨ БЕЗОПАСНОЕ СОЗДАНИЕ (ЗАЩИТА ОТ ДУБЛИКАТОВ) ✨ ---
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
        # Если база данных ругается, что такой ID уже есть,
        # значит мы его пропустили при поиске (или это дубль в самом файле).
        # Просто находим его и возвращаем.
        try:
            existing_student = Student.objects.get(student_id=final_id)
            return existing_student, False, False
        except Student.DoesNotExist:
            # Если и так не нашли (что странно при IntegrityError), пробрасываем ошибку
            raise

# =============================================================================
# --- 1. ЗАГРУЗКА СПИСКА УЧЕНИКОВ (Импорт базы) ---
# =============================================================================

def process_student_upload(excel_file, school=None):
    """
    Универсальная загрузка списка студентов (RU, TJ, EN).
    АВТОМАТИЧЕСКИ СОЗДАЕТ ПОДКЛАССЫ (например, 7А), если существует параллель (7).
    """
    try:
        df = pd.read_excel(excel_file, dtype=str)
        df.columns = [normalize_header(col) for col in df.columns]
    except Exception as e:
        return {'errors': [f"Ошибка чтения Excel-файла: {e}"]}

    if df.empty:
        return {'errors': ["Файл пуст или не содержит данных."]}

    # Маппинг заголовков
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
        'surname': 'фамилия_en', 'surname (en)': 'фамилия_en', 
        'name': 'имя_en', 'name (en)': 'имя_en', 'first_name_en': 'имя_en'
    }
    df.rename(columns=column_mapping, inplace=True)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.fillna('').replace('nan', '')

    if 'student_id' not in df.columns:
        return {'errors': ["В файле ОБЯЗАТЕЛЬНО должна быть колонка 'ID' (или 'Code', 'Код')."]}

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    # Загружаем все классы
    if school:
        all_classes = SchoolClass.objects.filter(school=school).select_related('school', 'parent')
    else:
        all_classes = SchoolClass.objects.select_related('school', 'parent')

    # Кеш классов: "10А" -> [obj], "10" -> [obj]
    classes_cache = {}
    for cls in all_classes:
        norm_name = normalize_cyrillic(cls.name).replace(" ", "").strip().upper()
        if norm_name not in classes_cache: classes_cache[norm_name] = []
        classes_cache[norm_name].append(cls)

    with transaction.atomic():
        for index, row in df.iterrows():
            row_num = index + 2
            
            # --- Валидация ID ---
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
                student_id = student_id.zfill(6)
            if not student_id:
                skipped_count += 1
                continue

            # Поиск студента
            try:
                student = Student.objects.select_related('school_class__parent').get(student_id=student_id)
                student_exists = True
            except Student.DoesNotExist:
                student = None
                student_exists = False

            # --- Сбор данных ---
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

            # --- ✨ ЛОГИКА ПОИСКА И СОЗДАНИЯ КЛАССА ✨ ---
            target_cls = None
            class_name_raw = get_val('класс')
            
            if class_name_raw:
                # Нормализуем имя из Excel: "10 а" -> "10А"
                class_name_norm = normalize_cyrillic(class_name_raw).replace(" ", "").upper()
                
                # 1. Прямой поиск в кеше
                found_classes = classes_cache.get(class_name_norm)
                if found_classes:
                    target_cls = found_classes[0]
                
                # 2. Если класс не найден (например, 10А нет, есть только 10)
                # Пытаемся создать его автоматически!
                elif school: 
                    # Пробуем разделить на Цифру и Букву (например "10" и "А")
                    match = re.match(r'^(\d+)([А-ЯA-Z])$', class_name_norm)
                    if match:
                        parallel_name = match.group(1) # "10"
                        
                        # Ищем параллель "10" в кеше
                        parents = classes_cache.get(parallel_name)
                        if parents:
                            parent_cls = parents[0]
                            
                            # Создаем новый класс 10А
                            target_cls = SchoolClass.objects.create(
                                name=class_name_norm, # "10А"
                                school=school,
                                parent=parent_cls
                            )
                            # Добавляем в кеш
                            classes_cache[class_name_norm] = [target_cls]

            # --- ЛОГИКА ОБНОВЛЕНИЯ СТУДЕНТА ---
            if student_exists:
                changed = False
                for field, value in update_fields.items():
                    if getattr(student, field) != value:
                        setattr(student, field, value)
                        changed = True
                
                # Обновление класса
                if target_cls:
                    # Защита от даунгрейда (из 10А в 10 не переводим)
                    is_downgrade = False
                    if student.school_class.parent == target_cls:
                        is_downgrade = True
                    
                    if student.school_class != target_cls and not is_downgrade:
                         if school and student.school_class.school == target_cls.school:
                             student.school_class = target_cls
                             changed = True
                         elif not school: 
                             student.school_class = target_cls
                             changed = True

                if changed:
                    student.save()
                    updated_count += 1

            # --- ЛОГИКА СОЗДАНИЯ СТУДЕНТА ---
            else:
                if not target_cls:
                     if not class_name_raw:
                         errors.append(f"Строка {row_num}: Не указан класс для нового ученика {student_id}.")
                     else:
                         errors.append(f"Строка {row_num}: Класс '{class_name_raw}' (или его параллель) не найден в школе.")
                     continue
                
                if not ru_last or not ru_first:
                     ru_last = update_fields.get('last_name_en') or update_fields.get('last_name_tj') or 'Unknown'
                     ru_first = update_fields.get('first_name_en') or update_fields.get('first_name_tj') or 'Unknown'

                new_student = Student(
                    student_id=student_id,
                    school_class=target_cls,
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
# --- 2. АНАЛИЗ РЕЗУЛЬТАТОВ (ШАГ 1: ПРЕДПРОСМОТР) ---
# =============================================================================

def analyze_student_results(excel_file):
    """
    Читает файл, сохраняет его временно и ищет конфликты в ФИО.
    Использует ту же логику нормализации (Cyrillic + Title Case), что и сохранение.
    """
    # 1. Сохраняем файл временно
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    temp_file_name = f"temp_gat_upload_{timestamp}.xlsx"
    file_path = default_storage.save(f"temp/{temp_file_name}", excel_file)
    
    try:
        full_path = default_storage.path(file_path)
        df = pd.read_excel(full_path, dtype=str)
    except Exception as e:
        if default_storage.exists(file_path):
             default_storage.delete(file_path)
        return {'error': f"Ошибка чтения файла: {e}"}

    df.columns = [normalize_header(col) for col in df.columns]
    
    column_mapping = {
        'code': 'student_id', 'id': 'student_id', 'student_id': 'student_id',
        'код': 'student_id', 'рамз': 'student_id', 'id ученика': 'student_id',
        'surname': 'last_name', 'фамилия': 'last_name', 'насаб': 'last_name', 
        'lastname': 'last_name', 'surname_en': 'last_name',
        'name': 'first_name', 'имя': 'first_name', 'ном': 'first_name', 
        'firstname': 'first_name', 'name_en': 'first_name',
        # Можно добавить mapping для класса, если нужно выводить его в отчете
        'section': 'class_name', 'class': 'class_name', 'класс': 'class_name', 
        'синф': 'class_name', 'grade': 'class_name'
    }
    df.rename(columns=column_mapping, inplace=True)

    conflicts = []
    new_students_count = 0

    for index, row in df.iterrows():
        raw_id = row.get('student_id')
        
        # Надежная проверка на пустоту
        if pd.isna(raw_id) or str(raw_id).strip().lower() in ['nan', 'none', '', '0']: 
            continue
            
        student_id = str(raw_id).strip()
        if student_id.endswith('.0'): student_id = student_id[:-2]
        if student_id.isdigit() and not student_id.startswith('0') and len(student_id) < 6:
            student_id = student_id.zfill(6)
        
        # Ищем студента строго по ID для анализа конфликтов
        student = Student.objects.filter(student_id=student_id).first()

        # ✨ ВАЖНО: Применяем .title(), как и в функции сохранения!
        excel_last = normalize_cyrillic(str(row.get('last_name', '')).strip()).title()
        excel_first = normalize_cyrillic(str(row.get('first_name', '')).strip()).title()
        
        # Очистка от строковых 'nan'
        if excel_last.lower() == 'nan': excel_last = ''
        if excel_first.lower() == 'nan': excel_first = ''
        
        if not student:
            new_students_count += 1
        else:
            # Сравниваем только если в Excel есть данные
            if excel_last and excel_first:
                # Берем данные из БД "как есть"
                db_last = student.last_name_ru
                db_first = student.first_name_ru
                
                # Сравниваем. Так как excel_last мы уже прогнали через .title(),
                # то сравнение будет корректным.
                # Если в БД "Иванов", а в Excel "иванов" -> excel_last станет "Иванов" -> конфликта НЕТ (и это хорошо).
                # Если в БД "Иванов", а в Excel "Петров" -> конфликт ЕСТЬ.
                
                diff_last = db_last != excel_last
                diff_first = db_first != excel_first
                
                if diff_last or diff_first:
                    conflicts.append({
                        'student_id': student_id,
                        'current_last': db_last,
                        'current_first': db_first,
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
    Основная функция загрузки результатов GAT.
    ВКЛЮЧАЕТ ОПТИМИЗАЦИЮ: bulk_create для ответов и Smart Search для учеников.
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

            # --- ✨ 1. Ищем или создаем студента (УМНЫЙ ПОИСК) ---
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
        StudentAnswer.objects.filter(result_id__in=processed_result_ids).delete()
    
    if student_answers_to_create:
        StudentAnswer.objects.bulk_create(student_answers_to_create, batch_size=2000)

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