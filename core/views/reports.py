# D:\New_GAT\core\views\reports.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

import json
from collections import defaultdict
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.db.models import Count, Q
from openpyxl import Workbook
from weasyprint import HTML
from django.core.cache import cache
from .permissions import get_accessible_schools
from accounts.models import UserProfile

from core.models import (
    AcademicYear, GatTest, Quarter, QuestionCount, School,
    SchoolClass, Student, StudentResult, Subject
)
from core.forms import UploadFileForm, StatisticsFilterForm
from core.views.permissions import get_accessible_schools, get_accessible_subjects
from core import services
from core import utils

# --- UPLOAD AND DETAILED RESULTS ---

@login_required
def upload_results_view(request):
    """Загрузка результатов тестов с фильтрацией по дате из файла"""
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        test_date = None
        if 'file' in request.FILES:
            uploaded_file = request.FILES['file']
            test_date = services.extract_test_date_from_excel(uploaded_file)
            form = UploadFileForm(request.POST, request.FILES, test_date=test_date)

        if form.is_valid():
            gat_test = form.cleaned_data['gat_test']
            excel_file = request.FILES['file']

            try:
                success, report_data = services.process_student_results_upload(gat_test, excel_file)
                print(f"--- GAT UPLOAD REPORT: {report_data}")

                if success:
                    total = report_data.get('total_unique_students', 0)
                    errors = report_data.get('errors', [])
                    messages.success(
                        request,
                        f"Файл успешно обработан. Загружено результатов для {total} учеников."
                    )
                    for error in errors:
                        messages.error(request, error)
                    
                    base_url = reverse(
                        'core:detailed_results_list',
                        kwargs={'test_number': gat_test.test_number}
                    )
                    redirect_url = f"{base_url}?test_id={gat_test.id}"
                    return redirect(redirect_url)
                else:
                    messages.error(request, f"Ошибка обработки файла: {report_data}")

            except Exception as e:
                messages.error(
                    request,
                    f"Произошла критическая ошибка при обработке файла: {str(e)}"
                )
        else:
            messages.error(request, "Форма содержит ошибки. Проверьте введенные данные.")
    else:
        form = UploadFileForm()

    context = {
        'form': form,
        'title': 'Загрузка результатов GAT тестов'
    }
    return render(request, 'results/upload_form.html', context)

def get_detailed_results_data(test_number, request_get, request_user):
    """
    Готовит данные для детального рейтинга с улучшенной логикой фильтрации
    """
    year_id = request_get.get('year')
    quarter_id = request_get.get('quarter')
    school_id = request_get.get('school')
    class_id = request_get.get('class')
    test_id_from_upload = request_get.get('test_id')
    latest_test = None

    if test_id_from_upload:
        try:
            specific_test = GatTest.objects.filter(
                pk=test_id_from_upload,
                test_number=test_number
            ).select_related('quarter__year', 'school_class', 'school').first() # Добавил 'school'

            if specific_test:
                if not request_user.is_superuser:
                    accessible_schools = get_accessible_schools(request_user)
                    # Убедимся, что specific_test.school не None
                    if not specific_test.school or specific_test.school not in accessible_schools:
                        return { 'table_header': [], 'students_data': [], 'test': None } # Возвращаем пустой dict
                latest_test = specific_test
        except (ValueError, GatTest.DoesNotExist):
            pass

    if not latest_test:
        tests_qs = GatTest.objects.filter(test_number=test_number).select_related(
            'quarter__year', 'school_class', 'school'
        )

        if not request_user.is_superuser:
            accessible_schools = get_accessible_schools(request_user)
            tests_qs = tests_qs.filter(school__in=accessible_schools)

        filters = Q()
        if year_id and year_id != '0':
            filters &= Q(quarter__year_id=year_id)
        if quarter_id and quarter_id != '0':
            filters &= Q(quarter_id=quarter_id)
        if school_id and school_id != '0':
            filters &= Q(school_id=school_id)
        if class_id and class_id != '0':
            # Фильтруем по ID параллели (school_class)
            filters &= Q(school_class_id=class_id)

        if filters:
            tests_qs = tests_qs.filter(filters)

        latest_test = tests_qs.order_by('-test_date').first()

    if not latest_test:
        return {
            'table_header': [],
            'students_data': [],
            'test': None
        }

    student_results = StudentResult.objects.filter(
        gat_test=latest_test
    ).select_related('student__school_class', 'gat_test')

    # --- ✨✨✨ Логика `table_header` (которую ты исправил) здесь верная ✨✨✨ ---
    table_header = []
    if latest_test and latest_test.school_class:
        # 2. Получаем родительский класс (параллель), e.g., '5'
        #    (Если у класса есть родитель, берем его, иначе - сам класс)
        parent_class = latest_test.school_class.parent if latest_test.school_class.parent else latest_test.school_class

        # 3. Получаем предметы ТОЛЬКО ИЗ САМОГО ТЕСТА
        subjects_for_this_test = latest_test.subjects.all().order_by('name')

        # 4. Получаем ВСЕ QuestionCounts для этой параллели ОДНИМ запросом
        question_counts_map = {
            qc.subject_id: qc.number_of_questions
            for qc in QuestionCount.objects.filter(school_class=parent_class)
        }

        # 5. Cоздаем 'table_header' ТОЛЬКО для предметов из шага 3
        for subject in subjects_for_this_test:
            q_count = question_counts_map.get(subject.id, 0)
            table_header.append({
                'subject': subject,
                'questions': range(1, q_count + 1),
                'questions_count': q_count,
                'school_class': parent_class # Это параллель
            })
    # --- ✨✨✨ Конец логики `table_header` ✨✨✨ ---

    results_map = {res.student_id: res for res in student_results}
    students = Student.objects.filter(
        id__in=results_map.keys()
    ).select_related('school_class', 'school_class__school')

    students_data = []
    for student in students:
        result = results_map.get(student.id)
        total_score = result.total_score if result else 0
        subject_scores = {} # Эта логика не используется в `detailed_results_list.html`, но пусть будет

        if result and isinstance(result.scores_by_subject, dict):
            for subject_id_str, answers in result.scores_by_subject.items():
                try:
                    subject_id = int(subject_id_str)
                    
                    # ✨ ИСПРАВЛЕНИЕ: Читаем словарь ответов {'1': True, ...}
                    if isinstance(answers, dict):
                        correct_answers = sum(1 for v in answers.values() if v is True)
                        total_questions = len(answers)
                        subject_scores[subject_id] = {
                            'score': correct_answers,
                            'total_questions': total_questions,
                            'correct_answers': correct_answers
                        }
                    # (Старая логика для list, на всякий случай)
                    elif isinstance(answers, list):
                        correct_answers = sum(1 for v in answers if v is True)
                        total_questions = len(answers)
                        subject_scores[subject_id] = {
                            'score': correct_answers,
                            'total_questions': total_questions,
                            'correct_answers': correct_answers
                        }
                except (ValueError, TypeError):
                    continue

        students_data.append({
            'student': student,
            'result': result,
            'total_score': total_score,
            'subject_scores': subject_scores,
            'position': 0
        })

    students_data.sort(key=lambda x: x['total_score'], reverse=True)
    for idx, student_data in enumerate(students_data, 1):
        student_data['position'] = idx

    return {
        'table_header': table_header,
        'students_data': students_data,
        'test': latest_test
    }

# VVVVVV ОСНОВНОЕ ИСПРАВЛЕНИЕ ЗДЕСЬ VVVVVV
@login_required
def detailed_results_list_view(request, test_number):

    # 1. Получаем данные в виде СЛОВАРЯ.
    data = get_detailed_results_data(
        test_number, request.GET, request.user
    )

    # 2. Извлекаем данные из словаря по ключам.
    students_data = data['students_data']
    table_header = data['table_header']  # <-- ИСПОЛЬЗУЕМ ГОТОВЫЙ ЗАГОЛОВОК
    latest_test = data['test']         # <-- ПОЛУЧАЕМ ПРАВИЛЬНЫЙ ОБЪЕКТ ТЕСТА

    # --- УДАЛЕНО ---
    # ВЕСЬ БЛОК ПОВТОРНОГО СОЗДАНИЯ table_header УДАЛЕН,
    # так как он уже создан в get_detailed_results_data
    # (именно здесь была твоя ошибка, т.к. latest_test был строкой)
    # --- КОНЕЦ УДАЛЕНИЯ ---

    accessible_schools = get_accessible_schools(request.user) if not request.user.is_superuser else School.objects.all()

    context = {
        'title': f'Детальный рейтинг GAT-{test_number}',
        'students_data': students_data,
        'table_header': table_header, # <-- Теперь здесь ПРАВИЛЬНЫЙ заголовок
        'years': AcademicYear.objects.all().order_by('-start_date'),
        'schools': accessible_schools,
        'classes': SchoolClass.objects.filter(parent__isnull=True).order_by('name'), # Показываем только параллели
        'selected_year': request.GET.get('year'),
        'selected_quarter': request.GET.get('quarter'),
        'selected_school': request.GET.get('school'),
        'selected_class': request.GET.get('class'),
        'test_number': test_number,
        'test': latest_test, # <-- Теперь здесь ПРАВИЛЬНЫЙ объект
        'total_students': len(students_data),
        'max_score': max([s['total_score'] for s in students_data]) if students_data else 0
    }
    return render(request, 'results/detailed_results_list.html', context)
# ^^^^^^ КОНЕЦ ИСПРАВЛЕНИЯ ^^^^^^


@login_required
def student_result_detail_view(request, pk):
    result = get_object_or_404(
        StudentResult.objects.select_related(
            'student__school_class__school',
            'gat_test__quarter__year'
        ),
        pk=pk
    )

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if result.student.school_class.school not in accessible_schools:
            messages.error(request, "У вас нет доступа к этому результату.")
            return redirect('core:dashboard')

    subject_map = {s.id: s for s in Subject.objects.all()}
    processed_scores = {}
    total_correct = 0
    total_questions = 0

    # ✨ ИСПРАВЛЕНИЕ: Логика для чтения словаря {'1': True, ...}
    if isinstance(result.scores_by_subject, dict):
        for subject_id_str, answers in result.scores_by_subject.items():
            try:
                subject_id = int(subject_id_str)
                subject = subject_map.get(subject_id)
                if subject and isinstance(answers, dict): # Проверяем, что answers - словарь
                    total = len(answers)
                    correct = sum(1 for v in answers.values() if v is True)
                    processed_scores[subject.name] = {
                        # Передаем словарь { '1': True, ... }
                        'answers_dict': answers, 
                        'total': total, 
                        'correct': correct,
                        'incorrect': total - correct, 
                        'percentage': round((correct / total) * 100, 1) if total > 0 else 0,
                        'subject': subject
                    }
                    total_correct += correct
                    total_questions += total
            except (ValueError, TypeError):
                continue

    overall_percentage = round((total_correct / total_questions) * 100, 1) if total_questions > 0 else 0

    context = {
        'result': result, 'processed_scores': processed_scores, 'title': f'Детальный отчет: {result.student}',
        'total_correct': total_correct, 'total_questions': total_questions, 'overall_percentage': overall_percentage
    }
    return render(request, 'results/student_result_detail.html', context)

@login_required
def student_result_delete_view(request, pk):
    result = get_object_or_404(StudentResult, pk=pk)
    test_number = result.gat_test.test_number

    if request.method == 'POST':
        student_name = str(result.student)
        test_info = f"GAT-{test_number}"
        try:
            result.delete()
            messages.success(request, f'Результат для "{student_name}" (тест {test_info}) был успешно удален.')
            
            # ✨ ИСПРАВЛЕНИЕ: Добавляем query-параметр test_id для возврата
            base_url = reverse('core:detailed_results_list', kwargs={'test_number': test_number})
            redirect_url = f"{base_url}?test_id={result.gat_test_id}" # Возвращаемся к удаленному тесту

            return redirect(redirect_url)
        except Exception as e:
            messages.error(request, f'Ошибка при удалении результата: {str(e)}')
            return redirect('core:student_result_detail', pk=pk)

    context = {
        'item': result, 'title': f'Удалить результат: {result.student}',
        'cancel_url': reverse('core:student_result_detail', kwargs={'pk': pk}),
        'test_number': test_number
    }
    return render(request, 'results/confirm_delete_result.html', context)

# --- ARCHIVE AND COMPARISON ---

@login_required
def archive_years_view(request):
    """Архив по годам с улучшенной и исправленной статистикой"""
    user = request.user
    accessible_schools = get_accessible_schools(user)
    results_qs = StudentResult.objects.filter(
        student__school_class__school__in=accessible_schools
    )
    year_ids_with_results = results_qs.values_list('gat_test__quarter__year_id', flat=True).distinct()
    years = AcademicYear.objects.filter(
        id__in=year_ids_with_results
    ).annotate(
        test_count=Count(
            'quarters__gat_tests',
            filter=Q(
                quarters__gat_tests__results__isnull=False,
                quarters__gat_tests__school__in=accessible_schools
            ),
            distinct=True
        ),
        student_count=Count(
            'quarters__gat_tests__results__student',
            filter=Q(
                quarters__gat_tests__school__in=accessible_schools
            ),
            distinct=True
        )
    ).order_by('-start_date')

    context = {
        'years': years,
        'title': 'Архив результатов по годам'
    }
    return render(request, 'results/archive_years.html', context)

@login_required
def archive_quarters_view(request, year_id):
    """Архив по четвертям с исправленной статистикой"""
    year = get_object_or_404(AcademicYear, id=year_id)
    user = request.user
    accessible_schools = get_accessible_schools(user)
    quarters = Quarter.objects.filter(
        year=year,
        gat_tests__results__isnull=False,
        gat_tests__school__in=accessible_schools
    ).annotate(
        test_count=Count(
            'gat_tests',
            filter=Q(gat_tests__school__in=accessible_schools),
            distinct=True
        ),
        school_count=Count(
            'gat_tests__school',
            filter=Q(gat_tests__school__in=accessible_schools),
            distinct=True
        )
    ).distinct().order_by('start_date')

    context = {
        'year': year,
        'quarters': quarters,
        'title': f'Архив: {year.name}'
    }
    return render(request, 'results/archive_quarters.html', context)

@login_required
def archive_schools_view(request, quarter_id):
    """Архив по школам с исправленной статистикой"""
    quarter = get_object_or_404(Quarter, id=quarter_id)
    user = request.user
    accessible_schools = get_accessible_schools(user)

    # VVVVVV НАЧАЛО ИЗМЕНЕННОГО ЗАПРОСА VVVVVV
    # 1. Фильтруем школы:
    #    - Доступные пользователю
    #    - Имеющие классы -> учеников -> результаты -> тесты в нужной четверти
    schools = School.objects.filter(
        id__in=accessible_schools.values_list('id', flat=True), # Доступные школы
        classes__students__results__gat_test__quarter=quarter  # Есть результаты в этой четверти
    ).distinct().annotate( # distinct() ДО annotate
        # 2. Считаем УНИКАЛЬНЫЕ КЛАССЫ, у которых есть студенты с результатами в этой четверти
        class_count=Count(
            'classes', # Путь: School -> classes (SchoolClass)
            filter=Q(
                # Условие: у класса есть студенты с результатами в этой четверти
                classes__students__results__gat_test__quarter=quarter
            ),
            distinct=True
        ),
        # 3. Считаем УНИКАЛЬНЫХ СТУДЕНТОВ, у которых есть результаты в этой четверти
        student_count=Count(
            'classes__students', # Путь: School -> classes -> students (Student)
            filter=Q(
                # Условие: у студента есть результаты в этой четверти
                classes__students__results__gat_test__quarter=quarter
            ),
            distinct=True
        )
    ).order_by('name') # Сортируем по имени школы
    # ^^^^^^ КОНЕЦ ИЗМЕНЕННОГО ЗАПРОСА ^^^^^^

    context = {
        'quarter': quarter,
        'schools': schools, # Передаем queryset schools
        'title': f'Архив: {quarter}'
    }
    return render(request, 'results/archive_schools.html', context)

@login_required
def archive_classes_view(request, quarter_id, school_id):
    """
    Отображает родительские классы (параллели),
    в которых есть результаты за выбранную четверть.
    """
    quarter = get_object_or_404(Quarter, id=quarter_id)
    school = get_object_or_404(School, id=school_id)

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if school not in accessible_schools:
            messages.error(request, "У вас нет доступа к архиву этой школы.")
            return redirect('core:results_archive')

    # Находим ID родительских классов (параллелей),
    # у которых есть тесты в этой четверти
    parent_class_ids = GatTest.objects.filter(
        quarter_id=quarter_id,
        school_id=school_id,
        results__isnull=False # У теста есть хотя бы 1 результат
    ).values_list('school_class_id', flat=True).distinct()

    parent_classes = SchoolClass.objects.filter(
        id__in=parent_class_ids,
        school=school # Убедимся, что параллель из той же школы
    ).order_by('name')

    context = {
        'quarter': quarter,
        'school': school,
        'parent_classes': parent_classes,
        'title': f'Архив: {school.name} (Выберите параллель)'
    }
    return render(request, 'results/archive_classes.html', context)

def _get_data_for_test(gat_test):
    """
    Вспомогательная функция: получает данные для ОДНОГО теста.
    (Исправлена: читает `table_header` из `get_detailed_results_data`)
    """
    if not gat_test:
        return [], []

    # Используем существующую функцию, чтобы получить данные
    # Передаем "пустой" GET-запрос и системного юзера (для прав доступа)
    # Это неоптимально, но использует существующую логику
    
    # --- ✨ ИСПРАВЛЕНИЕ: Мы не можем вызывать get_detailed_results_data
    # без request.GET и request.user.
    # Вместо этого, продублируем упрощенную логику. ---

    student_results = StudentResult.objects.filter(
        gat_test=gat_test
    ).select_related('student__school_class__school')

    students_data = []
    for result in student_results:
        students_data.append({
            'student': result.student,
            'result': result,
            'total_score': result.total_score,
            'subject_scores': {}, # Заполняется по необходимости
            'position': 0
        })

    students_data.sort(key=lambda x: x['total_score'], reverse=True)
    for idx, student_data in enumerate(students_data, 1):
        student_data['position'] = idx

    # --- Логика `table_header` (такая же, как в `get_detailed_results_data`) ---
    table_header = []
    if gat_test and gat_test.school_class:
        parent_class = gat_test.school_class.parent if gat_test.school_class.parent else gat_test.school_class
        subjects_for_this_test = gat_test.subjects.all().order_by('name')
        question_counts_map = {
            qc.subject_id: qc.number_of_questions
            for qc in QuestionCount.objects.filter(school_class=parent_class)
        }
        for subject in subjects_for_this_test:
            q_count = question_counts_map.get(subject.id, 0)
            table_header.append({
                'subject': subject,
                'questions': range(1, q_count + 1),
                'questions_count': q_count,
                'school_class': parent_class
            })
            
    return students_data, table_header

@login_required
def class_results_dashboard_view(request, quarter_id, class_id):
    """
    (Исправлено: `class_id` - это ID подкласса, e.g. 5А)
    """
    school_class = get_object_or_404(SchoolClass, id=class_id) # Это 5А
    quarter = get_object_or_404(Quarter, id=quarter_id)
    parent_class = school_class.parent # Это 5 (параллель)

    if not parent_class:
        messages.error(request, f"Класс {school_class.name} не является подклассом (не имеет параллели).")
        return redirect('core:results_archive')

    try:
        gat_number = int(request.GET.get('gat_number', 1))
    except ValueError:
        gat_number = 1

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if not accessible_schools.filter(id=school_class.school.id).exists():
            messages.error(request, "У вас нет доступа к отчетам этого класса.")
            return redirect('core:results_archive')

    # Ищем тесты по ПАРАЛЛЕЛИ (e.g. '5')
    test_day1 = GatTest.objects.filter(
        school_class=parent_class,
        quarter=quarter,
        test_number=gat_number,
        day=1
    ).prefetch_related('subjects').first()

    test_day2 = GatTest.objects.filter(
        school_class=parent_class,
        quarter=quarter,
        test_number=gat_number,
        day=2
    ).prefetch_related('subjects').first()

    all_students_data_day1, table_header_day1 = _get_data_for_test(test_day1)
    all_students_data_day2, table_header_day2 = _get_data_for_test(test_day2)
    
    # (Блок фильтрации заголовков не нужен, т.к. _get_data_for_test уже это делает)

    # Фильтруем данные по студентам ТЕКУЩЕГО КЛАССА (e.g. '5А')
    students_data_day1 = [
        data for data in all_students_data_day1
        if data['student'].school_class_id == school_class.id # Сравниваем по ID
    ]
    students_data_day2 = [
        data for data in all_students_data_day2
        if data['student'].school_class_id == school_class.id # Сравниваем по ID
    ]

    all_students_map = {}
    for data in students_data_day1:
        student = data['student']
        if student.id not in all_students_map:
            all_students_map[student.id] = {'student': student, 'score1': None, 'score2': None}
        all_students_map[student.id]['score1'] = data['total_score']
        all_students_map[student.id]['result1'] = data.get('result')
    for data in students_data_day2:
        student = data['student']
        if student.id not in all_students_map:
            all_students_map[student.id] = {'student': student, 'score1': None, 'score2': None}
        all_students_map[student.id]['score2'] = data['total_score']
        all_students_map[student.id]['result2'] = data.get('result')

    students_data_total = []
    # ✨ ИСПРАВЛЕНИЕ: Итерируем по всем студентам из all_students_map
    # (а не только тем, кто есть в day1)
    for student_id, data in all_students_map.items():
        score1, score2 = data.get('score1'), data.get('score2')
        total_score = (score1 or 0) + (score2 or 0) if score1 is not None or score2 is not None else None
        progress = score2 - score1 if score1 is not None and score2 is not None else None

        students_data_total.append({
            'student': data['student'], 'score1': score1, 'score2': score2,
            'total_score': total_score, 'progress': progress, 'result1': data.get('result1'),
            'result2': data.get('result2'), 'display_class': data['student'].school_class.name
        })
    students_data_total.sort(key=lambda x: (x['total_score'] is None, -x['total_score'] if x['total_score'] is not None else 0))

    total_students = len(students_data_total)
    participated_both = len([s for s in students_data_total if s['score1'] is not None and s['score2'] is not None])
    avg_score1_list = [s['score1'] for s in students_data_total if s['score1'] is not None]
    avg_score2_list = [s['score2'] for s in students_data_total if s['score2'] is not None]
    avg_score1 = sum(avg_score1_list) / len(avg_score1_list) if avg_score1_list else 0
    avg_score2 = sum(avg_score2_list) / len(avg_score2_list) if avg_score2_list else 0

    context = {
        'title': f'Отчет класса: {school_class.name}',
        'school_class': school_class, # Это 5А
        'parent_class': parent_class, # Это 5
        'quarter': quarter,
        'students_data_total': students_data_total,
        'students_data_gat1': students_data_day1,
        'table_header_gat1': table_header_day1,
        'students_data_gat2': students_data_day2,
        'table_header_gat2': table_header_day2,
        'test_day1': test_day1,
        'test_day2': test_day2,
        'gat_number_choices': GatTest.TEST_NUMBER_CHOICES,
        'selected_gat_number': gat_number,
        'stats': {
            'total_students': total_students, 'participated_both': participated_both,
            'avg_score1': round(avg_score1, 1), 'avg_score2': round(avg_score2, 1),
            'avg_progress': round(avg_score2 - avg_score1, 1)
        }
    }
    return render(request, 'results/class_results_dashboard.html', context)

@login_required
def compare_class_tests_view(request, test1_id, test2_id):
    """Сравнение двух тестов с улучшенной логикой ранжирования"""
    test1 = get_object_or_404(GatTest.objects.prefetch_related('subjects'), id=test1_id)
    test2 = get_object_or_404(GatTest.objects.prefetch_related('subjects'), id=test2_id)

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if (not test1.school or test1.school not in accessible_schools or
            not test2.school or test2.school not in accessible_schools):
            messages.error(request, "У вас нет доступа для сравнения этих тестов.")
            return redirect('core:dashboard')

    student_ids_test1 = StudentResult.objects.filter(gat_test=test1).values_list('student_id', flat=True)
    student_ids_test2 = StudentResult.objects.filter(gat_test=test2).values_list('student_id', flat=True)
    all_student_ids = set(student_ids_test1) | set(student_ids_test2)

    all_students = Student.objects.filter(
        id__in=all_student_ids
    ).select_related('school_class__school').order_by('last_name_ru', 'first_name_ru')

    results1_map = {res.student_id: res for res in StudentResult.objects.filter(gat_test=test1)}
    results2_map = {res.student_id: res for res in StudentResult.objects.filter(gat_test=test2)}

    # ✨ ИСПРАВЛЕНИЕ: Используем `total_score` из объекта StudentResult
    full_scores1 = [{
        'student': student,
        'score': results1_map[student.id].total_score if student.id in results1_map else 0,
        'present': student.id in results1_map
    } for student in all_students]

    full_scores2 = [{
        'student': student,
        'score': results2_map[student.id].total_score if student.id in results2_map else 0,
        'present': student.id in results2_map
    } for student in all_students]

    sorted_scores1 = sorted([s for s in full_scores1 if s['present']], key=lambda x: x['score'], reverse=True)
    sorted_scores2 = sorted([s for s in full_scores2 if s['present']], key=lambda x: x['score'], reverse=True)

    rank_map1 = {item['student'].id: rank + 1 for rank, item in enumerate(sorted_scores1)}
    rank_map2 = {item['student'].id: rank + 1 for rank, item in enumerate(sorted_scores2)}

    comparison_results = []
    for student in all_students:
        is_present1 = student.id in results1_map
        is_present2 = student.id in results2_map
        rank1 = rank_map1.get(student.id)
        rank2 = rank_map2.get(student.id)
        score1 = next((s['score'] for s in full_scores1 if s['student'].id == student.id), None)
        score2 = next((s['score'] for s in full_scores2 if s['student'].id == student.id), None)
        avg_rank = (rank1 + rank2) / 2 if is_present1 and is_present2 else float('inf')
        progress = score2 - score1 if is_present1 and is_present2 else None

        comparison_results.append({
            'student': student,
            'score1': score1 if is_present1 else '—',
            'score2': score2 if is_present2 else '—',
            'rank1': rank1 if is_present1 else '—',
            'rank2': rank2 if is_present2 else '—',
            'avg_rank': round(avg_rank, 1) if avg_rank != float('inf') else '—',
            'progress': progress,
            'participation': get_participation_type(is_present1, is_present2)
        })

    comparison_results.sort(key=lambda x: (x['avg_rank'] == '—', x['avg_rank'] if x['avg_rank'] != '—' else float('inf')))
    students_data_1, table_header_1 = _get_data_for_test(test1)
    students_data_2, table_header_2 = _get_data_for_test(test2)

    context = {
        'results': comparison_results,
        'test1': test1, 'test2': test2,
        'title': f'Сравнение тестов: {test1.school_class.name if test1.school_class else "класса"}',
        'students_data_1': students_data_1, 'table_header_1': table_header_1,
        'students_data_2': students_data_2, 'table_header_2': table_header_2,
    }
    return render(request, 'results/comparison_detail.html', context)

def get_participation_type(present1, present2):
    """Вспомогательная функция для определения типа участия"""
    if present1 and present2: return "Оба теста"
    elif present1: return "Только GAT-1"
    elif present2: return "Только GAT-2"
    else: return "Не участвовал"

# --- MAIN REPORTING VIEWS ---

@login_required
def analysis_view(request):
    """
    (Исправлена логика обработки словаря `answers` в `agg_data`)
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    form = StatisticsFilterForm(request.GET or None, user=user)
    selected_quarter_ids_str = request.GET.getlist('quarters')
    selected_school_ids_str = request.GET.getlist('schools')
    selected_class_ids_str = request.GET.getlist('school_classes')
    selected_subject_ids_str = request.GET.getlist('subjects')
    final_grouped_classes = {}

    if request.GET:
        grouped_classes = defaultdict(list)
        if selected_school_ids_str:
            try:
                school_ids_int = [int(sid) for sid in selected_school_ids_str]
                classes_qs = SchoolClass.objects.filter(
                    school_id__in=school_ids_int
                ).select_related('parent', 'school').order_by('school__name', 'name')
                is_multiple_schools = len(school_ids_int) > 1
                for cls in classes_qs:
                    group_name = f"{cls.parent.name} классы" if cls.parent else f"{cls.name} классы (Параллель)"
                    if is_multiple_schools:
                        group_name = f"{cls.school.name} - {group_name}"
                    grouped_classes[group_name].append(cls)
                sorted_group_items = sorted(
                    grouped_classes.items(),
                    key=lambda item: (not item[0].endswith("(Параллель)"), item[0])
                )
                for group_name, classes_in_group in sorted_group_items:
                    classes_in_group.sort(key=lambda x: x.name)
                    final_grouped_classes[group_name] = classes_in_group
            except ValueError:
                messages.error(request, "Некорректный ID школы в параметрах.")
                pass

    context = {
        'title': 'Анализ успеваемости', 'form': form, 'has_results': False,
        'grouped_classes': final_grouped_classes,
        'selected_quarter_ids': selected_quarter_ids_str, 'selected_school_ids': selected_school_ids_str,
        'selected_class_ids': selected_class_ids_str, 'selected_subject_ids': selected_subject_ids_str,
        'table_headers': [], 'table_data': {}, 'subject_averages': {}, 'subject_ranks': {},
        'chart_labels': '[]', 'chart_datasets': '[]',
        'selected_class_ids_json': json.dumps(selected_class_ids_str),
        'selected_subject_ids_json': json.dumps(selected_subject_ids_str),
    }

    if form.is_valid():
        selected_quarters = form.cleaned_data['quarters']
        selected_schools = form.cleaned_data['schools']
        selected_classes_qs = form.cleaned_data['school_classes']
        selected_test_numbers = form.cleaned_data['test_numbers']
        selected_days = form.cleaned_data['days']
        selected_subjects_qs = form.cleaned_data['subjects']
        selected_class_ids_list_int = list(selected_classes_qs.values_list('id', flat=True))
        parent_class_ids_int = selected_classes_qs.filter(parent__isnull=True).values_list('id', flat=True)
        if parent_class_ids_int:
            child_class_ids_int = list(SchoolClass.objects.filter(parent_id__in=parent_class_ids_int).values_list('id', flat=True))
            selected_class_ids_list_int.extend(child_class_ids_int)
        final_class_ids_int = set(selected_class_ids_list_int)
        accessible_schools = get_accessible_schools(user)
        results_qs = StudentResult.objects.filter(
            student__school_class__school__in=accessible_schools
        ).select_related('student__school_class', 'gat_test__quarter__year')

        if selected_quarters: results_qs = results_qs.filter(gat_test__quarter__in=selected_quarters)
        if selected_schools: results_qs = results_qs.filter(student__school_class__school__in=selected_schools)
        if final_class_ids_int: results_qs = results_qs.filter(student__school_class_id__in=final_class_ids_int)
        if selected_test_numbers: results_qs = results_qs.filter(gat_test__test_number__in=selected_test_numbers)
        if selected_days: results_qs = results_qs.filter(gat_test__day__in=selected_days)

        accessible_subjects_qs = Subject.objects.none()
        is_expert = profile and profile.role == UserProfile.Role.EXPERT
        expert_subject_ids_int = set()
        if is_expert:
            expert_subjects = profile.subjects.all()
            expert_subject_ids_int = set(expert_subjects.values_list('id', flat=True))
            if selected_subjects_qs.exists():
                accessible_subjects_qs = selected_subjects_qs.filter(id__in=expert_subject_ids_int)
            elif expert_subjects.exists():
                accessible_subjects_qs = expert_subjects
            elif not accessible_subjects_qs.exists():
                 results_qs = results_qs.none()
        else:
            accessible_subjects_qs = selected_subjects_qs

        if results_qs.exists():
            if accessible_subjects_qs.exists():
                subject_id_keys_to_filter = [str(s.id) for s in accessible_subjects_qs]
                results_qs = results_qs.filter(scores_by_subject__has_any_keys=subject_id_keys_to_filter)
            elif is_expert:
                 results_qs = results_qs.none()

        if not accessible_subjects_qs.exists() and not is_expert and results_qs.exists():
             all_subject_ids_in_results = set()
             for r in results_qs.only('scores_by_subject'): # Оптимизация
                 if isinstance(r.scores_by_subject, dict):
                     all_subject_ids_in_results.update(int(sid) for sid in r.scores_by_subject.keys())
             accessible_subjects_qs = Subject.objects.filter(id__in=all_subject_ids_in_results)

        if results_qs.exists() and accessible_subjects_qs.exists():
            subject_map = {s.id: s.name for s in accessible_subjects_qs}
            allowed_subject_ids_int = set(subject_map.keys())
            agg_data = defaultdict(lambda: defaultdict(lambda: {'correct': 0, 'total': 0}))

            results_qs = results_qs.prefetch_related('student__school_class')

            for result in results_qs:
                class_name = result.student.school_class.name
                if isinstance(result.scores_by_subject, dict):
                    for subject_id_str, answers in result.scores_by_subject.items():
                        try:
                            subject_id = int(subject_id_str)
                            if subject_id in allowed_subject_ids_int:
                                subject_name = subject_map.get(subject_id)
                                
                                # --- ✨ ИСПРАВЛЕНИЕ: Читаем словарь ответов {'1': True, ...}
                                if subject_name and isinstance(answers, dict):
                                    correct_answers = sum(1 for v in answers.values() if v is True)
                                    total_questions = len(answers) # Кол-во вопросов = кол-во ключей
                                    agg_data[class_name][subject_name]['correct'] += correct_answers
                                    agg_data[class_name][subject_name]['total'] += total_questions
                                # --- ✨ КОНЕЦ ИСПРАВЛЕНИЯ ---
                        except (ValueError, TypeError):
                            continue

            table_data = defaultdict(dict)
            all_subjects = set(accessible_subjects_qs.values_list('name', flat=True))
            all_classes = sorted(agg_data.keys())

            for class_name, subjects_data in agg_data.items():
                for subject_name, scores in subjects_data.items():
                    if scores['total'] > 0:
                        percentage = round((scores['correct'] / scores['total']) * 100, 1)
                        table_data[subject_name][class_name] = percentage

            subject_averages = {}
            for subject_name in all_subjects:
                scores = [score for class_name in all_classes if (score := table_data.get(subject_name, {}).get(class_name)) is not None]
                if scores:
                    subject_averages[subject_name] = round(sum(scores) / len(scores), 1)
            
            sorted_subjects_by_avg = sorted(subject_averages.items(), key=lambda item: item[1], reverse=True)
            subject_ranks = { name: rank + 1 for rank, (name, avg) in enumerate(sorted_subjects_by_avg) }
            sorted_subjects_list = sorted(list(all_subjects))
            chart_datasets = [{
                'label': class_name,
                'data': [table_data.get(subject_name, {}).get(class_name, 0) for subject_name in sorted_subjects_list]
            } for class_name in all_classes]
            sorted_table_data = {subject: table_data.get(subject, {}) for subject in sorted_subjects_list}

            context.update({
                'has_results': True, 'table_headers': all_classes, 'table_data': sorted_table_data,
                'subject_averages': subject_averages, 'subject_ranks': subject_ranks,
                'chart_labels': json.dumps(sorted_subjects_list, ensure_ascii=False),
                'chart_datasets': json.dumps(chart_datasets, ensure_ascii=False),
            })

    context['selected_class_ids_json'] = json.dumps(selected_class_ids_str)
    context['selected_subject_ids_json'] = json.dumps(selected_subject_ids_str)

    return render(request, 'analysis.html', context)

# --- EXPORT FUNCTIONS ---

@login_required
def export_detailed_results_excel(request, test_number):
    """Экспорт результатов в Excel с улучшенным форматированием"""
    # ✨ ИСПРАВЛЕНИЕ: Получаем данные как словарь
    data = get_detailed_results_data(
        test_number, request.GET, request.user
    )
    students_data = data['students_data']
    table_header = data['table_header']
    test_info = data['test']
    # ---

    if not students_data:
        messages.warning(request, "Нет данных для экспорта.")
        return redirect('core:detailed_results_list', test_number=test_number)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f"GAT-{test_number}_results_{test_info.test_date if test_info else ''}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = f'GAT-{test_number} Результаты'

    headers = ["№", "ID", "ФИО Студента", "Класс", "Школа"]
    for header in table_header:
        subject_name = header['subject'].abbreviation or header['subject'].name[:3].upper()
        for i in range(1, header['questions_count'] + 1):
            headers.append(f"{subject_name}_{i}")
    headers.extend(["Общий балл", "Позиция в рейтинге"])
    sheet.append(headers)

    for idx, data in enumerate(students_data, 1):
        row = [
            idx,
            data['student'].student_id,
            data['student'].full_name_ru, # Используем .full_name_ru
            data['student'].school_class.name,
            data['student'].school_class.school.name
        ]

        result = data.get('result')
        if result and isinstance(result.scores_by_subject, dict):
            for header in table_header:
                subject_id = str(header['subject'].id)
                # ✨ ИСПРАВЛЕНИЕ: Читаем словарь ответов {'1': True, ...}
                answers_dict = result.scores_by_subject.get(subject_id, {})
                q_count = header['questions_count']
                
                # Заполняем по номерам вопросов
                answers_list = []
                for i in range(1, q_count + 1):
                    answer = answers_dict.get(str(i)) # Ищем по ключу '1', '2', ...
                    if answer is True:
                        answers_list.append(1)
                    elif answer is False:
                        answers_list.append(0)
                    else:
                        answers_list.append('') # Пусто, если ответа нет
                
                row.extend(answers_list)
                # ---
        else:
            for header in table_header:
                row.extend([''] * header['questions_count'])

        row.extend([data['total_score'], data['position']])
        sheet.append(row)

    workbook.save(response)
    return response

@login_required
def export_detailed_results_pdf(request, test_number):
    """Экспорт результатов в PDF с улучшенным оформлением"""
    # ✨ ИСПРАВЛЕНИЕ: Получаем данные как словарь
    data = get_detailed_results_data(
        test_number, request.GET, request.user
    )
    # ---

    if not data['students_data']:
        messages.warning(request, "Нет данных для экспорта.")
        return redirect('core:detailed_results_list', test_number=test_number)

    context = {
        'title': f'Детальный рейтинг GAT-{test_number}',
        'students_data': data['students_data'],
        'table_header': data['table_header'],
        'test_info': data['test'],
        'export_date': utils.get_current_date(),
        'total_students': len(data['students_data'])
    }

    html_string = render_to_string('results/detailed_results_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"GAT-{test_number}_results_{data['test'].test_date if data['test'] else ''}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    try:
        HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
        return response
    except Exception as e:
        messages.error(request, f"Ошибка при создании PDF: {str(e)}")
        return redirect('core:detailed_results_list', test_number=test_number)

@login_required
def archive_subclasses_view(request, quarter_pk, school_pk, class_pk):
    """
    (Исправлено: class_pk - это ID параллели)
    """
    quarter = get_object_or_404(Quarter, id=quarter_pk)
    school = get_object_or_404(School, id=school_pk)
    parent_class = get_object_or_404(SchoolClass, id=class_pk) # Это 5

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if school not in accessible_schools:
            messages.error(request, "У вас нет доступа к этой школе.")
            return redirect('core:results_archive')

    # Находим подклассы (5А, 5Б), у которых есть ученики с результатами
    subclasses = SchoolClass.objects.filter(
        parent=parent_class,
        school=school,
        students__results__gat_test__quarter=quarter
    ).distinct().order_by('name')

    context = {
        'quarter': quarter,
        'school': school,
        'parent_class': parent_class,
        'subclasses': subclasses,
        'title': f'Архив: {school.name} - {parent_class.name} классы'
    }
    return render(request, 'results/archive_subclasses.html', context)


@login_required
def combined_class_report_view(request, quarter_id, parent_class_id):
    """
    Формирует сводный отчет с вкладками для Дня 1 и Дня 2 ОДНОГО GAT теста.
    (Исправленная логика)
    """
    quarter = get_object_or_404(Quarter, id=quarter_id)
    parent_class = get_object_or_404(SchoolClass, id=parent_class_id, parent__isnull=True) # Убедимся, что это параллель

    # --- ✨ ИЗМЕНЕНИЕ 1: Получаем номер GAT из запроса (по умолчанию GAT-1) ---
    try:
        gat_number = int(request.GET.get('gat_number', 1))
    except ValueError:
        gat_number = 1
    # ---

    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        if parent_class.school not in accessible_schools:
            messages.error(request, "У вас нет доступа к этому отчету.")
            return redirect('core:results_archive')

    # --- ✨ ИЗМЕНЕНИЕ 2: Ищем День 1 и День 2 для выбранного gat_number ---
    test_day1 = GatTest.objects.filter(
        school_class=parent_class,
        quarter=quarter,
        test_number=gat_number, # Используем выбранный номер GAT
        day=1                   # Ищем День 1
    ).prefetch_related('subjects').first() # Добавил prefetch_related

    test_day2 = GatTest.objects.filter(
        school_class=parent_class,
        quarter=quarter,
        test_number=gat_number, # Используем тот же номер GAT
        day=2                   # Ищем День 2
    ).prefetch_related('subjects').first() # Добавил prefetch_related
    # ---

    # Находим ID всех учеников в этой параллели (во всех подклассах 5А, 5Б, ...)
    student_ids_in_parallel = set(Student.objects.filter(
        school_class__parent=parent_class
    ).values_list('id', flat=True))

    # --- ✨ ИЗМЕНЕНИЕ 3: Переименовываем переменные для ясности ---
    all_data_day1, table_header_day1 = _get_data_for_test(test_day1)
    students_data_day1 = [data for data in all_data_day1 if data['student'].id in student_ids_in_parallel]

    all_data_day2, table_header_day2 = _get_data_for_test(test_day2)
    students_data_day2 = [data for data in all_data_day2 if data['student'].id in student_ids_in_parallel]
    # ---

    all_students_map = {}
    # Используем students_data_day1 и students_data_day2
    for data in students_data_day1:
        student = data['student']
        # Сохраняем result для ссылок
        all_students_map[student.id] = {'student': student, 'score1': data['total_score'], 'score2': None, 'result1': data.get('result')}

    for data in students_data_day2:
        student = data['student']
        if student.id not in all_students_map:
            # Если ученик не сдавал День 1, добавляем его
            all_students_map[student.id] = {'student': student, 'score1': None, 'result1': None}
        all_students_map[student.id]['score2'] = data['total_score']
        all_students_map[student.id]['result2'] = data.get('result') # Сохраняем result для ссылок

    students_data_total = []
    for data in all_students_map.values():
        score1, score2 = data.get('score1'), data.get('score2')
        total_score = (score1 or 0) + (score2 or 0) if score1 is not None or score2 is not None else None
        progress = score2 - score1 if score1 is not None and score2 is not None else None

        students_data_total.append({
            'student': data['student'], 'score1': score1, 'score2': score2,
            'total_score': total_score, 'progress': progress,
            'result1': data.get('result1'), # Передаем result для ссылок
            'result2': data.get('result2'), # Передаем result для ссылок
            # ✨ ИЗМЕНЕНИЕ 4: Добавляем display_class для total_table.html
            'display_class': data['student'].school_class.name
        })
    students_data_total.sort(key=lambda x: (x['total_score'] is None, -x['total_score'] if x['total_score'] is not None else 0))

    context = {
        # --- ✨ ИЗМЕНЕНИЕ 5: Обновляем title и переменные контекста ---
        'title': f'Общий рейтинг GAT-{gat_number}: {parent_class.name} классы',
        'parent_class': parent_class,
        'quarter': quarter,
        'students_data_total': students_data_total, # Для вкладки "Итоговый"
        'students_data_day1': students_data_day1,     # Для вкладки "День 1"
        'table_header_day1': table_header_day1,
        'students_data_day2': students_data_day2,     # Для вкладки "День 2"
        'table_header_day2': table_header_day2,
        'test_day1': test_day1, # Передаем объект теста для Дня 1
        'test_day2': test_day2, # Передаем объект теста для Дня 2
        'gat_number_choices': GatTest.TEST_NUMBER_CHOICES, # Для фильтра
        'selected_gat_number': gat_number, # Для фильтра
        # ---
    }
    return render(request, 'results/combined_class_report.html', context)