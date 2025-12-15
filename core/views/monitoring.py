# D:\New_GAT\core\views\monitoring.py (ПОЛНАЯ ОБНОВЛЕННАЯ ВЕРСИЯ)

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment
from weasyprint import HTML
from django.template.loader import render_to_string

# Импортируем общую функцию из utils_reports
from .utils_reports import get_report_context
from ..models import SchoolClass # Импортируем SchoolClass для группировки

@login_required
def monitoring_view(request):
    """Отображает страницу Мониторинга с новой панелью фильтров."""
    # Используем общую функцию для получения данных
    context = get_report_context(request.GET, request.user, mode='monitoring')
    context['title'] = 'Мониторинг' # Устанавливаем заголовок

    # --- ✨ НАЧАЛО ИСПРАВЛЕНИЯ: Получаем выбранные ID для JavaScript ---
    # Получаем ID школ, классов и предметов из GET-запроса,
    # чтобы правильно отобразить выбранные "чипы" при перезагрузке страницы
    selected_school_ids_str = request.GET.getlist('schools')
    context['selected_class_ids'] = request.GET.getlist('school_classes')
    context['selected_subject_ids'] = request.GET.getlist('subjects') # <-- Получаем выбранные предметы

    # Конвертируем списки ID в JSON-строки для передачи в JavaScript
    context['selected_class_ids_json'] = json.dumps(context['selected_class_ids'])
    context['selected_subject_ids_json'] = json.dumps(context['selected_subject_ids']) # <-- Передаем JSON с предметами
    # --- ✨ КОНЕЦ ИСПРАВЛЕНИЯ ---

    # --- Логика для группировки классов в фильтре (без изменений) ---
    grouped_classes = defaultdict(list)
    if selected_school_ids_str:
        try:
            # Преобразуем строковые ID школ в целые числа
            school_ids_int = [int(sid) for sid in selected_school_ids_str]
            # Загружаем классы для выбранных школ
            classes_qs = SchoolClass.objects.filter(
                school_id__in=school_ids_int
            ).select_related('parent', 'school').order_by('school__name', 'name')

            # Определяем, выбрана ли одна или несколько школ
            is_multiple_schools = len(school_ids_int) > 1
            # Группируем классы по параллелям (и школам, если выбрано несколько)
            for cls in classes_qs:
                group_name = f"{cls.parent.name} классы" if cls.parent else f"{cls.name} классы (Параллель)"
                if is_multiple_schools:
                    group_name = f"{cls.school.name} - {group_name}"
                grouped_classes[group_name].append(cls)
        except ValueError:
            # Обработка ошибки, если ID школы некорректен
            pass # Можно добавить сообщение об ошибке

    # Сортируем группы классов для красивого отображения в фильтре
    final_grouped_classes = {}
    # Сначала параллели, потом остальные классы, всё по алфавиту
    sorted_group_items = sorted(grouped_classes.items(), key=lambda item: (not item[0].endswith("(Параллель)"), item[0]))
    for group_name, classes_in_group in sorted_group_items:
        classes_in_group.sort(key=lambda x: x.name) # Сортируем классы внутри группы
        final_grouped_classes[group_name] = classes_in_group

    # Добавляем сгруппированные классы в контекст для шаблона
    context['grouped_classes'] = final_grouped_classes
    # --- Конец логики группировки ---

    # Рендерим шаблон monitoring.html с подготовленным контекстом
    return render(request, 'monitoring/monitoring.html', context)

@login_required
def export_monitoring_pdf(request):
    """Экспортирует отчет по мониторингу в PDF."""
    # Используем общую функцию для получения данных
    context = get_report_context(request.GET, request.user, mode='monitoring')
    context['title'] = 'Отчет по мониторингу' # Устанавливаем заголовок для PDF

    # Рендерим HTML-шаблон для PDF в строку
    html_string = render_to_string('monitoring/monitoring_pdf.html', context)

    # Создаем HTTP-ответ с типом PDF
    response = HttpResponse(content_type='application/pdf')
    # Устанавливаем имя файла для скачивания
    response['Content-Disposition'] = 'attachment; filename="monitoring_report.pdf"'

    # Генерируем PDF из HTML с помощью WeasyPrint
    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response

@login_required
def export_monitoring_excel(request):
    """Экспортирует отчет по мониторингу в Excel."""
    # Используем общую функцию для получения данных
    context = get_report_context(request.GET, request.user, mode='monitoring')
    # Извлекаем заголовки и строки таблицы из контекста
    table_headers = context.get('table_headers', [])
    table_rows = context.get('table_rows', [])

    # Создаем HTTP-ответ с типом Excel
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # Устанавливаем имя файла для скачивания
    response['Content-Disposition'] = 'attachment; filename="monitoring_report.xlsx"'

    # Создаем книгу Excel
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Мониторинг' # Название листа

    # --- Формируем первую строку заголовка ---
    header1 = ["№", "ФИО Студента", "Класс", "Тест"]
    # Добавляем сокращения предметов
    for header_data in table_headers:
        header1.append(header_data['subject'].abbreviation or header_data['subject'].name)
    header1.append("Общий балл") # Добавляем итоговый столбец
    sheet.append(header1) # Записываем первую строку в лист

    # --- Формируем вторую строку заголовка ---
    header2 = ["", "", "", ""] # Пустые ячейки для первых столбцов
    # Добавляем информацию о максимальном балле (из q_count)
    for header_data in table_headers:
        q_count = header_data.get('q_count', 0)
        header2.append(f"(из {q_count})" if q_count > 0 else "") # Формат "(из N)"
    header2.append("") # Пустая ячейка для итогового столбца
    sheet.append(header2) # Записываем вторую строку

    # --- Объединяем ячейки в заголовке ---
    # Объединяем первые 4 столбца по вертикали (№, ФИО, Класс, Тест)
    for col in range(1, 5):
        sheet.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
        # Выравниваем текст по центру вертикально
        sheet.cell(row=1, column=col).alignment = Alignment(vertical='center')

    # Объединяем столбец "Общий балл" по вертикали
    total_score_col = len(header1) # Номер последнего столбца
    sheet.merge_cells(start_row=1, start_column=total_score_col, end_row=2, end_column=total_score_col)
    sheet.cell(row=1, column=total_score_col).alignment = Alignment(vertical='center')

    # --- Заполняем строки данными студентов ---
    for i, row_data in enumerate(table_rows, 1): # Нумеруем строки с 1
        # Формируем базовую часть строки
        row = [
            i, # Номер по порядку
            row_data['student'].full_name_ru, # ФИО студента
            str(row_data['student'].school_class), # Название класса
            row_data['result_obj'].gat_test.name if row_data.get('result_obj') else "Total" # Название теста
        ]
        # Добавляем баллы по предметам в формате "балл/всего"
        for header_data in table_headers:
            # Извлекаем данные о баллах для текущего предмета
            score_data = row_data.get('scores_by_subject', {}).get(header_data['subject'].id)
            # Формируем значение ячейки
            if score_data and score_data.get('score') != '—':
                cell_value = f"{score_data.get('score', 0)}/{score_data.get('total', 0)}" # Формат "X/Y"
            else:
                cell_value = "—" # Если данных нет
            row.append(cell_value) # Добавляем значение в строку

        # Добавляем общий балл
        row.append(row_data['total_score'])
        # Записываем всю строку в лист Excel
        sheet.append(row)

    # --- Автоподбор ширины столбцов ---
    for col_idx, column_cells in enumerate(sheet.columns, 1): # Итерируем по столбцам
        max_length = 0
        column = get_column_letter(col_idx) # Получаем букву столбца (A, B, ...)
        # Находим максимальную длину текста в столбце
        for cell in column_cells:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass # Пропускаем ошибки, если значение не строка
        # Устанавливаем ширину столбца (макс. длина + небольшой запас)
        adjusted_width = (max_length + 2)
        sheet.column_dimensions[column].width = adjusted_width

    # Сохраняем книгу Excel в HTTP-ответ
    workbook.save(response)
    return response