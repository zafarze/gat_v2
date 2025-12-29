# D:\New_GAT\core\views\grading.py

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment

# Импорт утилит и моделей
from .utils_reports import get_report_context
from ..models import SchoolClass

@login_required
def grading_view(request):
    """Отображает страницу 'Таблица оценок' с фильтрами."""
    
    # Получаем контекст (данные таблицы, фильтры и т.д.)
    context = get_report_context(request.GET, request.user, mode='grading')
    context['title'] = 'Таблица оценок'

    # Обработка выбранных фильтров для передачи обратно в шаблон (для JS)
    selected_school_ids_str = request.GET.getlist('schools')
    context['selected_class_ids'] = request.GET.getlist('school_classes')
    context['selected_class_ids_json'] = json.dumps(context['selected_class_ids'])
    
    context['selected_subject_ids'] = request.GET.getlist('subjects')
    context['selected_subject_ids_json'] = json.dumps(context['selected_subject_ids'])
    
    # Логика группировки классов (для красивого отображения в фильтре)
    grouped_classes = defaultdict(list)
    if selected_school_ids_str:
        try:
            school_ids_int = [int(sid) for sid in selected_school_ids_str]
            classes_qs = SchoolClass.objects.filter(
                school_id__in=school_ids_int
            ).select_related('parent', 'school').order_by('school__name', 'name')
            
            is_multiple_schools = len(school_ids_int) > 1
            for cls in classes_qs:
                # Формируем имя группы (например "5 классы" или "Школа А - 5 классы")
                group_name = f"{cls.parent.name} классы" if cls.parent else f"{cls.name} классы (Параллель)"
                if is_multiple_schools:
                    group_name = f"{cls.school.name} - {group_name}"
                grouped_classes[group_name].append(cls)
        except ValueError:
            pass
    
    # Сортировка групп классов
    final_grouped_classes = {}
    sorted_group_items = sorted(grouped_classes.items(), key=lambda item: (not item[0].endswith("(Параллель)"), item[0]))
    for group_name, classes_in_group in sorted_group_items:
        classes_in_group.sort(key=lambda x: x.name)
        final_grouped_classes[group_name] = classes_in_group
        
    context['grouped_classes'] = final_grouped_classes

    return render(request, 'grading/grading.html', context)


@login_required
def export_grading_pdf(request):
    """Экспортирует отчет по оценкам в PDF."""
    # Получаем данные
    context = get_report_context(request.GET, request.user, mode='grading')
    context['title'] = 'Отчет по оценкам'
    
    # Рендерим HTML для PDF
    html_string = render_to_string('grading/grading_pdf.html', context) 
    
    # Генерируем PDF
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="grading_report.pdf"'
    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response


@login_required
def export_grading_excel(request):
    """
    Экспортирует отчет по оценкам в Excel.
    Использует явный словарь переводов (Dictionary) вместо gettext
    для гарантированной смены языка.
    """
    # 1. Получаем данные отчета
    context = get_report_context(request.GET, request.user, mode='grading')
    table_headers = context.get('table_headers', [])
    table_rows = context.get('table_rows', [])
    
    # 2. Получаем язык из URL (например: ?...&lang=en). По умолчанию 'ru'.
    lang = request.GET.get('lang', 'ru')

    # 3. Словарь всех текстов для Excel
    translations = {
        'ru': {
            'sheet_title': "Таблица оценок",
            'no': "№", 
            'student': "ФИО Студента", 
            'class': "Класс", 
            'test': "Тест", 
            'total_header': "Общий балл", 
            'points_label': "(10 баллов)",
            'gat_total': "GAT Total"
        },
        'en': {
            'sheet_title': "Grading Table",
            'no': "#", 
            'student': "Student Name", 
            'class': "Class", 
            'test': "Test", 
            'total_header': "Total Score", 
            'points_label': "(10 points)",
            'gat_total': "GAT Total"
        },
        'tj': {
            'sheet_title': "Ҷадвали баҳоҳо",
            'no': "№", 
            'student': "Ному насаб", 
            'class': "Синф", 
            'test': "Тест", 
            'total_header': "Холи умумӣ", 
            'points_label': "(10 балл)",
            'gat_total': "GAT Умумӣ"
        }
    }
    
    # Выбираем нужный словарь (если языка нет, берем RU)
    t = translations.get(lang, translations['ru'])

    # Создаем Excel файл
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="grading_report.xlsx"'
    
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = t['sheet_title']
    
    # --- СТРОКА ЗАГОЛОВКОВ 1 (Названия колонок) ---
    header1 = [t['no'], t['student'], t['class'], t['test']]
    # Добавляем предметы
    for header_data in table_headers: 
        # Используем аббревиатуру или имя предмета
        header1.append(header_data['subject'].abbreviation or header_data['subject'].name)
    header1.append(t['total_header'])
    
    sheet.append(header1)
    
    # --- СТРОКА ЗАГОЛОВКОВ 2 (Подписи "(10 баллов)") ---
    header2 = ["", "", "", ""]
    for _ in table_headers: 
        header2.append(t['points_label'])
    header2.append("")
    
    sheet.append(header2)
    
    # --- ОФОРМЛЕНИЕ ЗАГОЛОВКОВ (Объединение ячеек) ---
    # Объединяем первые 4 колонки (№, Имя, Класс, Тест) по вертикали
    for col in range(1, 5):
        sheet.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
        sheet.cell(row=1, column=col).alignment = Alignment(vertical='center')
    
    # Объединяем колонку "Общий балл" по вертикали
    total_score_col = len(header1)
    sheet.merge_cells(start_row=1, start_column=total_score_col, end_row=2, end_column=total_score_col)
    sheet.cell(row=1, column=total_score_col).alignment = Alignment(vertical='center')
    
    # --- ЗАПОЛНЕНИЕ ДАННЫМИ ---
    for i, row_data in enumerate(table_rows, 1):
        # Подсчет суммы баллов (суммируем только числа, игнорируем "—")
        total_grade_score = sum(filter(lambda v: isinstance(v, (int, float)), row_data['grades_by_subject'].values()))
        
        # Определяем название теста
        if row_data.get('is_total'):
            test_name = t['gat_total']
        else:
            result_obj = row_data.get('result_obj')
            test_name = result_obj.gat_test.name if result_obj else ''

        # ВЫБОР ИМЕНИ СТУДЕНТА В ЗАВИСИМОСТИ ОТ ЯЗЫКА
        student = row_data['student']
        if lang == 'en':
            student_name = student.full_name_en or student.full_name_ru
        elif lang == 'tj':
            student_name = student.full_name_tj or student.full_name_ru
        else:
            student_name = student.full_name_ru
        
        # Формируем строку
        row = [
            i, 
            student_name, 
            str(student.school_class),
            test_name 
        ]
        
        # Добавляем оценки по предметам
        for header_data in table_headers:
            grade = row_data['grades_by_subject'].get(header_data['subject'].id, "—")
            row.append(grade)
            
        row.append(total_grade_score)
        sheet.append(row)
        
    # --- АВТОМАТИЧЕСКАЯ ШИРИНА КОЛОНОК ---
    for col_idx, column_cells in enumerate(sheet.columns, 1):
        max_length = 0
        column = get_column_letter(col_idx)
        for cell in column_cells:
            try:
                if len(str(cell.value)) > max_length: 
                    max_length = len(str(cell.value))
            except: 
                pass
        # Добавляем немного отступа
        sheet.column_dimensions[column].width = (max_length + 3)
        
    workbook.save(response)
    return response