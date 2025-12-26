# D:\Project Archive\GAT\core\services.py

import pandas as pd
import re
import uuid
import logging
import openpyxl
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
        # ДОБАВЛЕНО: engine='openpyxl'
        df = pd.read_excel(file_path_or_obj, dtype=str, engine='openpyxl')
        
        # Улучшенная обработка колонок:
        new_cols = []
        for col in df.columns:
            norm_col = normalize_header(col)
            # Если это стандартная колонка (ID, Имя) - нормализуем
            if norm_col in COLUMN_MAPPING:
                new_cols.append(COLUMN_MAPPING[norm_col])
            else:
                # Если это предмет (ALGEBRA_1) - оставляем как есть, только убираем пробелы
                new_cols.append(str(col).strip())
        
        df.columns = new_cols
        
        # Убираем дубликаты и очищаем пустые значения
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.fillna('').replace(['nan', 'None', 'NULL'], '')
        return df, None
    except Exception as e:
        logger.error(f"Error reading excel: {e}")
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
    match = re.match(r'^(\d+)([А-ЯA-Z])$', class_name_norm)
    if match and school:
        parallel_name = match.group(1) # "10"
        parent_cls = classes_cache.get(parallel_name)
        
        if parent_cls and parent_cls.parent is None:
            try:
                new_cls = SchoolClass.objects.create(
                    name=class_name_norm, # "10А"
                    school=school,
                    parent=parent_cls
                )
                classes_cache[class_name_norm] = new_cls
                return new_cls
            except IntegrityError:
                return SchoolClass.objects.filter(school=school, name=class_name_norm).first()
    
    return None

# =============================================================================
# --- ЛОГИКА СТУДЕНТОВ (ПОИСК И СОЗДАНИЕ) ---
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

    classes_qs = SchoolClass.objects.select_related('parent')
    if school:
        classes_qs = classes_qs.filter(school=school)
    
    classes_cache = {}
    for c in classes_qs:
        norm_name = normalize_cyrillic(c.name).replace(" ", "").upper()
        classes_cache[norm_name] = c

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


def analyze_results_file(gat_test, file_path):
    """
    Анализирует файл перед загрузкой:
    1. Ищет конфликты имен.
    2. Ищет переводы между классами.
    3. Ищет новых учеников.
    """
    full_path = default_storage.path(file_path)
    df, error = _read_excel_to_df(full_path)
    
    if error: return {'error': error}

    analysis = {
        'total_rows': len(df),
        'new_students': [],
        'name_conflicts': [],
        'transfers': [],
        'error': None
    }
    
    school = gat_test.school
    
    test_grade_prefix = ""
    if gat_test.school_class:
        if gat_test.school_class.parent:
            test_grade_prefix = gat_test.school_class.parent.name
        else:
            test_grade_prefix = gat_test.school_class.name

    processed_ids = set()

    existing_students = {
        s.student_id: s 
        for s in Student.objects.filter(school_class__school=school)
    }

    for index, row in df.iterrows():
        try:
            raw_id = row.get('student_id')
            if not raw_id: continue
            
            st_id = str(raw_id).strip()
            if st_id.endswith('.0'): st_id = st_id[:-2]
            if st_id.isdigit() and len(st_id) < 6: st_id = st_id.zfill(6)
            
            if st_id in processed_ids: continue
            processed_ids.add(st_id)

            excel_class_name = str(row.get('class_name', '')).strip()
            if len(excel_class_name) == 1 and test_grade_prefix:
                excel_class_name = test_grade_prefix + excel_class_name
            
            excel_last = normalize_cyrillic(row.get('last_name_ru', '')).title()
            excel_first = normalize_cyrillic(row.get('first_name_ru', '')).title()
            excel_full_name = f"{excel_last} {excel_first}"

            if st_id in existing_students:
                student = existing_students[st_id]
                db_full_name = f"{student.last_name_ru} {student.first_name_ru}"
                
                if db_full_name != excel_full_name and excel_last and excel_first:
                    analysis['name_conflicts'].append({
                        'id': st_id,
                        'current_name': db_full_name,
                        'new_name': excel_full_name,
                        'class': student.school_class.name
                    })

                if excel_class_name and student.school_class.name.upper() != excel_class_name.upper():
                    analysis['transfers'].append({
                        'id': st_id,
                        'name': db_full_name,
                        'old_class': student.school_class.name,
                        'new_class': excel_class_name
                    })
            else:
                analysis['new_students'].append({
                    'id': st_id,
                    'name': excel_full_name,
                    'class': excel_class_name or '?'
                })

        except Exception as e:
            continue

    return analysis

# =============================================================================
# --- АНАЛИЗ (PREVIEW) ---
# =============================================================================

def analyze_student_results(excel_file):
    """Предпросмотр файла (legacy/unused wrapper for students)."""
    # NOTE: This seems redundant given analyze_results_file above, but kept to avoid breaking imports
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    file_path = default_storage.save(f"temp/temp_gat_{timestamp}.xlsx", excel_file)
    full_path = default_storage.path(file_path)

    df, error = _read_excel_to_df(full_path)
    if error:
        default_storage.delete(file_path)
        return {'error': error}
    # ... logic truncated for brevity as it seems unused in reports.py ...
    return {'total_rows': len(df)}

# =============================================================================
# --- VALIDATION HELPERS ---
# =============================================================================

def extract_test_date_from_excel(file_obj):
    """
    Пытается извлечь дату теста из заголовка Excel-файла.
    """
    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active
        for row in sheet.iter_rows(min_row=1, max_row=10, max_col=10):
            for cell in row:
                value = cell.value
                if isinstance(value, datetime):
                    return value.date()
                if isinstance(value, str):
                    match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})|(\d{4}-\d{2}-\d{2})', value)
                    if match:
                        date_str = match.group(0).replace('/', '.').replace('-', '.')
                        for fmt in ('%d.%m.%Y', '%Y.%m.%d'):
                            try:
                                return datetime.strptime(date_str, fmt).date()
                            except ValueError:
                                continue
        return None
    except Exception as e:
        logger.error(f"Ошибка при извлечении даты из Excel: {e}")
        return None

def analyze_student_upload(file_path, school):
    """
    Читает файл и возвращает прогноз изменений для СТУДЕНТОВ.
    """
    full_path = default_storage.path(file_path)
    df, error = _read_excel_to_df(full_path)
    
    if error: return {'error': error}

    analysis = {
        'to_create': [],
        'to_update': [],
        'conflicts': [],
        'total_rows': len(df)
    }

    existing_students = {
        s.student_id: s 
        for s in Student.objects.filter(school_class__school=school)
    }

    for index, row in df.iterrows():
        raw_id = row.get('student_id')
        if not raw_id: continue
        st_id = str(raw_id).strip()
        if st_id.endswith('.0'): st_id = st_id[:-2]
        if st_id.isdigit() and len(st_id) < 6: st_id = st_id.zfill(6)

        new_last = normalize_cyrillic(row.get('last_name_ru', '')).title()
        new_first = normalize_cyrillic(row.get('first_name_ru', '')).title()
        class_name = row.get('class_name', '')

        if st_id in existing_students:
            student = existing_students[st_id]
            changes = []
            if new_last and student.last_name_ru != new_last:
                changes.append(f"Фамилия: {student.last_name_ru} -> {new_last}")
            if new_first and student.first_name_ru != new_first:
                changes.append(f"Имя: {student.first_name_ru} -> {new_first}")
            
            if changes:
                analysis['to_update'].append({
                    'id': st_id,
                    'name': f"{student.last_name_ru} {student.first_name_ru}",
                    'changes': ", ".join(changes)
                })
        else:
            analysis['to_create'].append({
                'id': st_id,
                'name': f"{new_last} {new_first}",
                'class': class_name
            })

    return analysis

# =============================================================================
# --- СОХРАНЕНИЕ РЕЗУЛЬТАТОВ (ГЛАВНАЯ ФУНКЦИЯ) ---
# =============================================================================

def process_student_results_upload(gat_test, file_path, overrides_map=None):
    """
    Читает Excel, ОБНОВЛЯЕТ классы учеников, ОБНОВЛЯЕТ имена (если разрешено)
    и СОХРАНЯЕТ оценки.
    """
    full_path = default_storage.path(file_path)
    df, error = _read_excel_to_df(full_path)
    
    if error: return False, {'errors': [error]}

    report = {'total_unique_students': 0, 'errors': []}
    school = gat_test.school
    
    test_grade_prefix = ""
    if gat_test.school_class:
        if gat_test.school_class.parent:
            test_grade_prefix = gat_test.school_class.parent.name
        else:
            test_grade_prefix = gat_test.school_class.name
            
    existing_classes = {c.name.upper(): c for c in SchoolClass.objects.filter(school=school)}
    
    subjects_map = {}
    for s in Subject.objects.all():
        if s.abbreviation: 
            subjects_map[s.abbreviation.upper()] = s
        
        # Нормализованное имя (МАТЕМАТИКА)
        norm_name = normalize_cyrillic(s.name).upper()
        subjects_map[norm_name] = s 
        # Первые 3 буквы (МАТ)
        subjects_map[norm_name[:3]] = s
        # Оригинальное английское имя (ALGEBRA)
        subjects_map[s.name.upper().strip()] = s

    allowed_name_updates = overrides_map.get('update_names_list', []) if overrides_map else []
    allowed_class_transfers = overrides_map.get('update_class_ids', []) if overrides_map else []

    processed_ids = set()

    with transaction.atomic():
        for index, row in df.iterrows():
            try:
                # --- А. ID Ученика ---
                raw_id = row.get('student_id')
                if not raw_id: continue
                st_id = str(raw_id).strip()
                if st_id.endswith('.0'): st_id = st_id[:-2]
                if st_id.isdigit() and len(st_id) < 6: st_id = st_id.zfill(6)
                if st_id in processed_ids: continue

                excel_class_name = str(row.get('class_name', '')).strip()
                excel_last = normalize_cyrillic(row.get('last_name_ru', '')).title()
                excel_first = normalize_cyrillic(row.get('first_name_ru', '')).title()

                # --- Б. Класс ---
                target_class = None
                if excel_class_name:
                    if len(excel_class_name) == 1 and test_grade_prefix:
                        cls_key = (test_grade_prefix + excel_class_name).upper()
                    else:
                        cls_key = excel_class_name.upper()

                    if cls_key in existing_classes:
                        target_class = existing_classes[cls_key]
                    else:
                        match = re.match(r'^(\d+)', cls_key)
                        parent_cls = None
                        if match:
                            par_name = match.group(1)
                            parent_cls = SchoolClass.objects.filter(school=school, name=par_name, parent__isnull=True).first()
                            if not parent_cls:
                                parent_cls = SchoolClass.objects.create(school=school, name=par_name)
                        target_class = SchoolClass.objects.create(school=school, name=cls_key, parent=parent_cls)
                        existing_classes[cls_key] = target_class

                # --- В. Ученик ---
                student, created = Student.objects.get_or_create(
                    student_id=st_id,
                    defaults={
                        'first_name_ru': excel_first or 'Unknown',
                        'last_name_ru': excel_last or 'Unknown',
                        'school_class': target_class or gat_test.school_class,
                        'status': 'ACTIVE'
                    }
                )

                # --- Г. Обновление данных ---
                updated = False
                if excel_last and excel_first:
                    should_update_name = created or (st_id in allowed_name_updates)
                    if should_update_name:
                        if student.last_name_ru != excel_last:
                            student.last_name_ru = excel_last; updated = True
                        if student.first_name_ru != excel_first:
                            student.first_name_ru = excel_first; updated = True
                
                if target_class and student.school_class != target_class:
                    should_transfer = created or (st_id in allowed_class_transfers)
                    if should_transfer:
                        student.school_class = target_class; updated = True
                
                if updated: student.save()

                # --- Д. Парсинг Оценок (ИСПРАВЛЕНО) ---
                scores_by_subject = defaultdict(dict)
                total_score = 0
                for col in df.columns:
                    # Разделяем по ПОСЛЕДНЕМУ подчеркиванию (rsplit), чтобы поддерживать имена типа ALIFBOI_NIYOGON_1
                    parts = col.rsplit('_', 1)
                    
                    if len(parts) == 2 and parts[1].isdigit():
                        subj_name_raw = parts[0].upper() # ALGEBRA
                        q_num = parts[1] # 1
                        
                        # Пробуем найти предмет
                        normalized_key = normalize_cyrillic(subj_name_raw)
                        subject = subjects_map.get(normalized_key)
                        if not subject:
                             subject = subjects_map.get(subj_name_raw)
                        
                        if subject:
                            val = str(row[col]).strip()
                            is_correct = val in ['1', '1.0', '+', 'True', 'TRUE']
                            if is_correct or val in ['0', '0.0', '-', 'False', 'FALSE']:
                                scores_by_subject[str(subject.id)][str(q_num)] = is_correct
                                if is_correct: total_score += 1

                StudentResult.objects.filter(student=student, gat_test=gat_test).delete()
                StudentResult.objects.create(
                    student=student, gat_test=gat_test,
                    scores_by_subject=dict(scores_by_subject), total_score=total_score
                )
                processed_ids.add(st_id)
                report['total_unique_students'] += 1

            except Exception as e:
                report['errors'].append(f"Строка {index + 2}: {str(e)}")

    return True, report