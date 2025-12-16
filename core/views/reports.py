# D:\New_GAT\core\views\reports.py (ПОЛНАЯ БОЕВАЯ ВЕРСИЯ)

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
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment
from weasyprint import HTML

from accounts.models import UserProfile
from core.models import (
    AcademicYear, GatTest, Quarter, QuestionCount, School,
    SchoolClass, Student, StudentResult, Subject
)
from core.forms import UploadFileForm, StatisticsFilterForm
from core.views.permissions import get_accessible_schools
from core import services
from core import utils

# =============================================================================
# --- 1. ЗАГРУЗКА И ДЕТАЛЬНЫЙ РЕЙТИНГ ---
# =============================================================================

@login_required
def upload_results_view(request):
    """
    Двухэтапная загрузка:
    1. Анализ (поиск конфликтов ФИО).
    2. Подтверждение и сохранение.
    """
    if request.method == 'POST':
        # ✨ ИСПРАВЛЕНИЕ: Проверяем оба варианта флага подтверждения
        # 1. confirm_upload='true' (из шаблона upload_review.html)
        # 2. step='confirm' (на случай, если используется другая логика)
        is_confirm = (request.POST.get('confirm_upload') == 'true') or (request.POST.get('step') == 'confirm')

        # === ШАГ 2: ПОДТВЕРЖДЕНИЕ И ЗАГРУЗКА ===
        if is_confirm:
            # Получаем путь к файлу из скрытого поля
            file_path = request.POST.get('file_path')
            
            # Получаем ID теста. В шаблоне поле может называться 'gat_test' или 'gat_test_id'
            gat_test_id = request.POST.get('gat_test') or request.POST.get('gat_test_id')
            
            gat_test = get_object_or_404(GatTest, pk=gat_test_id)

            # Собираем решения пользователя (какие имена обновлять)
            overrides = {}
            for key, value in request.POST.items():
                if key.startswith('decision_'):
                    student_id = key.replace('decision_', '')
                    overrides[student_id] = value # 'db' или 'excel'

            try:
                # Запускаем финальную обработку по ПУТИ к файлу
                success, report = services.process_student_results_upload(gat_test, file_path, overrides)

                if success:
                    msg = f"Загрузка завершена! Обработано: {report['total_unique_students']}. Создано: {report['created_students']}. Обновлено имен: {report['updated_names']}."
                    messages.success(request, msg)
                    if report.get('errors'):
                        for err in report['errors']:
                            messages.warning(request, err)
                else:
                    # Если process_student_results_upload вернул False
                    error_msg = report.get('errors', ['Неизвестная ошибка'])[0]
                    messages.error(request, f"Ошибка: {error_msg}")

                # Редирект на список результатов
                return redirect(f"{reverse('core:detailed_results_list', args=[gat_test.test_number])}?test_id={gat_test.id}")
            
            except Exception as e:
                messages.error(request, f"Критическая ошибка при сохранении: {e}")
                return redirect('core:upload_results')

        # === ШАГ 1: АНАЛИЗ (По умолчанию) ===
        else:
            # Пытаемся получить дату из файла для фильтрации формы (если нужно)
            test_date = None
            if 'file' in request.FILES:
                test_date = services.extract_test_date_from_excel(request.FILES['file'])
            
            form = UploadFileForm(request.POST, request.FILES, test_date=test_date)

            if form.is_valid():
                gat_test = form.cleaned_data['gat_test']
                excel_file = request.FILES['file']

                # 1. Запускаем АНАЛИЗ (возвращает путь к временному файлу и конфликты)
                analysis = services.analyze_student_results(excel_file)

                if 'error' in analysis:
                    messages.error(request, analysis['error'])
                    return redirect('core:upload_results')

                conflicts = analysis['conflicts']
                file_path = analysis['file_path'] # Путь к сохраненному временному файлу

                # Если конфликтов НЕТ -> Сразу сохраняем
                # (Можно убрать этот блок, если хотите всегда показывать Review страницу)
                if not conflicts:
                    success, report = services.process_student_results_upload(gat_test, file_path, {})
                    if success:
                        messages.success(request, f"Успешно! Обработано строк: {report['total_unique_students']}. Новых: {report['created_students']}.")
                    else:
                         # Если process_student_results_upload вернул False
                        error_msg = report.get('errors', ['Неизвестная ошибка'])[0]
                        messages.error(request, f"Ошибка: {error_msg}")
                    
                    return redirect(f"{reverse('core:detailed_results_list', args=[gat_test.test_number])}?test_id={gat_test.id}")

                # Если конфликты ЕСТЬ -> Показываем страницу сравнения
                context = {
                    'conflicts': conflicts,
                    'file_path': file_path, 
                    'gat_test': gat_test,
                    'new_students_count': analysis['new_students_count'], # Исправлено имя ключа для шаблона
                    'total_rows': analysis.get('total_rows', 0)
                }
                return render(request, 'dashboard/reports/upload_review.html', context)
            
            else:
                # Ошибки валидации формы (например, не выбран файл)
                # messages.error(request, "Ошибка валидации формы.") # Можно не дублировать, форма сама покажет
                pass

    else:
        # Это блок GET запроса (открытие страницы)
        form = UploadFileForm()

    # Получаем список всех четвертей для фильтра
    all_quarters = Quarter.objects.select_related('year').order_by('-year__start_date', '-start_date')

    context = {
        'form': form,
        'title': 'Загрузка результатов GAT тестов',
        'all_quarters': all_quarters # <--- ДОБАВЛЯЕМ ЭТУ СТРОКУ
    }
    return render(request, 'results/upload_form.html', context)

def get_detailed_results_data(test_number, request_get, request_user):
    """
    Универсальная функция подготовки данных для детального рейтинга и экспорта.
    """
    year_id = request_get.get('year')
    quarter_id = request_get.get('quarter')
    school_id = request_get.get('school')
    class_id = request_get.get('class')
    test_id_from_upload = request_get.get('test_id')
    
    latest_test = None

    if test_id_from_upload:
        try:
            specific_test = GatTest.objects.select_related(
                'quarter__year', 'school_class', 'school'
            ).get(pk=test_id_from_upload, test_number=test_number)

            if request_user.is_superuser or specific_test.school in get_accessible_schools(request_user):
                latest_test = specific_test
        except GatTest.DoesNotExist:
            pass

    if not latest_test:
        tests_qs = GatTest.objects.filter(test_number=test_number).select_related(
            'quarter__year', 'school_class', 'school'
        )

        if not request_user.is_superuser:
            accessible_schools = get_accessible_schools(request_user)
            tests_qs = tests_qs.filter(school__in=accessible_schools)

        filters = Q()
        if year_id and year_id != '0': filters &= Q(quarter__year_id=year_id)
        if quarter_id and quarter_id != '0': filters &= Q(quarter_id=quarter_id)
        if school_id and school_id != '0': filters &= Q(school_id=school_id)
        if class_id and class_id != '0': filters &= Q(school_class_id=class_id)

        if filters:
            tests_qs = tests_qs.filter(filters)

        latest_test = tests_qs.order_by('-test_date').first()

    if not latest_test:
        return {'table_header': [], 'students_data': [], 'test': None}

    table_header = []
    if latest_test.school_class:
        parent_class = latest_test.school_class.parent if latest_test.school_class.parent else latest_test.school_class
        subjects_for_test = latest_test.subjects.all().order_by('name')

        qc_map = {
            qc.subject_id: qc.number_of_questions
            for qc in QuestionCount.objects.filter(school_class=parent_class)
        }

        for subject in subjects_for_test:
            q_count = qc_map.get(subject.id, 0)
            table_header.append({
                'subject': subject,
                'questions': range(1, q_count + 1),
                'questions_count': q_count,
                'school_class': parent_class
            })

    student_results = StudentResult.objects.filter(gat_test=latest_test).select_related(
        'student__school_class__school'
    )
    
    students_data = []
    for result in student_results:
        students_data.append({
            'student': result.student,
            'result': result,
            'total_score': result.total_score,
            'position': 0
        })

    students_data.sort(key=lambda x: x['total_score'], reverse=True)
    for idx, item in enumerate(students_data, 1):
        item['position'] = idx

    return {
        'table_header': table_header,
        'students_data': students_data,
        'test': latest_test
    }


@login_required
def detailed_results_list_view(request, test_number):
    """Отображение таблицы детального рейтинга."""
    data = get_detailed_results_data(test_number, request.GET, request.user)
    accessible_schools = get_accessible_schools(request.user) if not request.user.is_superuser else School.objects.all()

    context = {
        'title': f'Детальный рейтинг GAT-{test_number}',
        'students_data': data['students_data'],
        'table_header': data['table_header'],
        'test': data['test'],
        'test_number': test_number,
        'years': AcademicYear.objects.all().order_by('-start_date'),
        'schools': accessible_schools.order_by('name'),
        'classes': SchoolClass.objects.filter(parent__isnull=True).order_by('name'),
        'selected_year': request.GET.get('year'),
        'selected_quarter': request.GET.get('quarter'),
        'selected_school': request.GET.get('school'),
        'selected_class': request.GET.get('class'),
        'total_students': len(data['students_data']),
        'max_score': max([s['total_score'] for s in data['students_data']]) if data['students_data'] else 0
    }
    return render(request, 'results/detailed_results_list.html', context)


@login_required
def student_result_detail_view(request, pk):
    result = get_object_or_404(
        StudentResult.objects.select_related('student__school_class__school', 'gat_test__quarter__year'),
        pk=pk
    )

    if not request.user.is_superuser:
        if result.student.school_class.school not in get_accessible_schools(request.user):
            messages.error(request, "Нет доступа.")
            return redirect('core:dashboard')

    subject_map = {s.id: s for s in Subject.objects.all()}
    processed_scores = {}
    total_correct = 0
    total_questions = 0

    if isinstance(result.scores_by_subject, dict):
        for subject_id_str, answers in result.scores_by_subject.items():
            try:
                subject_id = int(subject_id_str)
                subject = subject_map.get(subject_id)
                if subject and isinstance(answers, dict):
                    count_total = len(answers) 
                    count_correct = sum(1 for v in answers.values() if v is True)
                    
                    processed_scores[subject.name] = {
                        'answers_dict': answers,
                        'total': count_total,
                        'correct': count_correct,
                        'incorrect': count_total - count_correct,
                        'percentage': round((count_correct / count_total) * 100, 1) if count_total > 0 else 0,
                        'subject': subject
                    }
                    total_correct += count_correct
                    total_questions += count_total
            except (ValueError, TypeError):
                continue

    overall_percentage = round((total_correct / total_questions) * 100, 1) if total_questions > 0 else 0

    context = {
        'result': result,
        'processed_scores': processed_scores,
        'title': f'Отчет: {result.student.full_name_ru}',
        'total_correct': total_correct,
        'total_questions': total_questions,
        'overall_percentage': overall_percentage
    }
    return render(request, 'results/student_result_detail.html', context)


@login_required
def student_result_delete_view(request, pk):
    result = get_object_or_404(StudentResult, pk=pk)
    test_number = result.gat_test.test_number
    test_id = result.gat_test.id

    if request.method == 'POST':
        try:
            result.delete()
            messages.success(request, f'Результат удален.')
            base_url = reverse('core:detailed_results_list', kwargs={'test_number': test_number})
            return redirect(f"{base_url}?test_id={test_id}")
        except Exception as e:
            messages.error(request, str(e))
            return redirect('core:student_result_detail', pk=pk)

    context = {
        'item': result, 
        'title': f'Удалить результат: {result.student}',
        'cancel_url': reverse('core:student_result_detail', kwargs={'pk': pk})
    }
    return render(request, 'results/confirm_delete_result.html', context)


# =============================================================================
# --- 2. АРХИВЫ ---
# =============================================================================

@login_required
def archive_years_view(request):
    user = request.user
    accessible_schools = get_accessible_schools(user)
    
    years = AcademicYear.objects.filter(
        quarters__gat_tests__results__student__school_class__school__in=accessible_schools
    ).annotate(
        test_count=Count('quarters__gat_tests', distinct=True),
        student_count=Count('quarters__gat_tests__results__student', distinct=True)
    ).distinct().order_by('-start_date')

    context = {'years': years, 'title': 'Архив по годам'}
    return render(request, 'results/archive_years.html', context)

@login_required
def archive_quarters_view(request, year_id):
    year = get_object_or_404(AcademicYear, id=year_id)
    user = request.user
    accessible_schools = get_accessible_schools(user)

    quarters = Quarter.objects.filter(
        year=year,
        gat_tests__results__isnull=False,
        gat_tests__school__in=accessible_schools
    ).annotate(
        test_count=Count('gat_tests', filter=Q(gat_tests__school__in=accessible_schools), distinct=True),
        school_count=Count('gat_tests__school', filter=Q(gat_tests__school__in=accessible_schools), distinct=True)
    ).order_by('start_date')

    context = {'year': year, 'quarters': quarters, 'title': f'Архив: {year.name}'}
    return render(request, 'results/archive_quarters.html', context)

@login_required
def archive_schools_view(request, quarter_id):
    quarter = get_object_or_404(Quarter, id=quarter_id)
    user = request.user
    accessible_schools = get_accessible_schools(user)

    schools = School.objects.filter(
        id__in=accessible_schools,
        classes__students__results__gat_test__quarter=quarter
    ).annotate(
        class_count=Count('classes', filter=Q(classes__students__results__gat_test__quarter=quarter), distinct=True),
        student_count=Count('classes__students', filter=Q(classes__students__results__gat_test__quarter=quarter), distinct=True)
    ).distinct().order_by('name')

    context = {'quarter': quarter, 'schools': schools, 'title': f'Архив: {quarter}'}
    return render(request, 'results/archive_schools.html', context)

@login_required
def archive_classes_view(request, quarter_id, school_id):
    quarter = get_object_or_404(Quarter, id=quarter_id)
    school = get_object_or_404(School, id=school_id)

    if not request.user.is_superuser and school not in get_accessible_schools(request.user):
        messages.error(request, "Нет доступа.")
        return redirect('core:results_archive')

    parent_class_ids = GatTest.objects.filter(
        quarter=quarter, school=school, results__isnull=False
    ).values_list('school_class_id', flat=True).distinct()

    parent_classes = SchoolClass.objects.filter(id__in=parent_class_ids).order_by('name')

    context = {'quarter': quarter, 'school': school, 'parent_classes': parent_classes, 'title': f'Архив: {school.name}'}
    return render(request, 'results/archive_classes.html', context)

@login_required
def archive_subclasses_view(request, quarter_pk, school_pk, class_pk):
    quarter = get_object_or_404(Quarter, id=quarter_pk)
    school = get_object_or_404(School, id=school_pk)
    parent_class = get_object_or_404(SchoolClass, id=class_pk)

    if not request.user.is_superuser and school not in get_accessible_schools(request.user):
        return redirect('core:results_archive')

    subclasses = SchoolClass.objects.filter(
        parent=parent_class, school=school,
        students__results__gat_test__quarter=quarter
    ).distinct().order_by('name')

    context = {
        'quarter': quarter, 'school': school, 'parent_class': parent_class,
        'subclasses': subclasses, 'title': f'Классы {parent_class.name}'
    }
    return render(request, 'results/archive_subclasses.html', context)


# =============================================================================
# --- 3. СРАВНИТЕЛЬНЫЕ ОТЧЕТЫ ---
# =============================================================================

def _get_data_for_test_obj(gat_test):
    if not gat_test:
        return [], []
    
    table_header = []
    if gat_test.school_class:
        parent = gat_test.school_class.parent or gat_test.school_class
        subjects = gat_test.subjects.all().order_by('name')
        qc_map = {qc.subject_id: qc.number_of_questions for qc in QuestionCount.objects.filter(school_class=parent)}
        
        for subj in subjects:
            count = qc_map.get(subj.id, 0)
            table_header.append({'subject': subj, 'questions': range(1, count+1), 'questions_count': count})

    results = StudentResult.objects.filter(gat_test=gat_test).select_related('student__school_class')
    data = []
    for r in results:
        data.append({'student': r.student, 'total_score': r.total_score, 'result': r})
    
    data.sort(key=lambda x: x['total_score'], reverse=True)
    return data, table_header

@login_required
def class_results_dashboard_view(request, quarter_id, class_id):
    school_class = get_object_or_404(SchoolClass, id=class_id)
    quarter = get_object_or_404(Quarter, id=quarter_id)
    parent_class = school_class.parent

    if not parent_class:
        messages.error(request, "Это не подкласс.")
        return redirect('core:results_archive')

    try: gat_number = int(request.GET.get('gat_number', 1))
    except: gat_number = 1

    test1 = GatTest.objects.filter(school_class=parent_class, quarter=quarter, test_number=gat_number, day=1).first()
    test2 = GatTest.objects.filter(school_class=parent_class, quarter=quarter, test_number=gat_number, day=2).first()

    data1, header1 = _get_data_for_test_obj(test1)
    data2, header2 = _get_data_for_test_obj(test2)

    data1 = [d for d in data1 if d['student'].school_class_id == school_class.id]
    data2 = [d for d in data2 if d['student'].school_class_id == school_class.id]

    students_map = {}
    for d in data1:
        sid = d['student'].id
        students_map[sid] = {'student': d['student'], 'score1': d['total_score'], 'result1': d['result'], 'score2': None, 'result2': None}
    
    for d in data2:
        sid = d['student'].id
        if sid not in students_map:
            students_map[sid] = {'student': d['student'], 'score1': None, 'result1': None, 'score2': None, 'result2': None}
        students_map[sid].update({'score2': d['total_score'], 'result2': d['result']})

    total_data = []
    for item in students_map.values():
        s1, s2 = item['score1'], item['score2']
        total = (s1 or 0) + (s2 or 0) if s1 is not None or s2 is not None else None
        progress = s2 - s1 if s1 is not None and s2 is not None else None
        item.update({'total_score': total, 'progress': progress, 'display_class': item['student'].school_class.name})
        total_data.append(item)
    
    total_data.sort(key=lambda x: (x['total_score'] is None, -x['total_score'] if x['total_score'] else 0))

    context = {
        'title': f'Отчет: {school_class.name}', 'school_class': school_class, 'quarter': quarter,
        'students_data_total': total_data, 'students_data_gat1': data1, 'students_data_gat2': data2,
        'table_header_gat1': header1, 'table_header_gat2': header2,
        'test_day1': test1, 'test_day2': test2, 'selected_gat_number': gat_number, 'gat_number_choices': GatTest.TEST_NUMBER_CHOICES
    }
    return render(request, 'results/class_results_dashboard.html', context)

@login_required
def combined_class_report_view(request, quarter_id, parent_class_id):
    quarter = get_object_or_404(Quarter, id=quarter_id)
    parent_class = get_object_or_404(SchoolClass, id=parent_class_id)
    
    try: gat_number = int(request.GET.get('gat_number', 1))
    except: gat_number = 1

    test1 = GatTest.objects.filter(school_class=parent_class, quarter=quarter, test_number=gat_number, day=1).first()
    test2 = GatTest.objects.filter(school_class=parent_class, quarter=quarter, test_number=gat_number, day=2).first()

    data1, header1 = _get_data_for_test_obj(test1)
    data2, header2 = _get_data_for_test_obj(test2)

    students_map = {}
    for d in data1:
        students_map[d['student'].id] = {'student': d['student'], 'score1': d['total_score'], 'result1': d['result'], 'score2': None, 'result2': None}
    for d in data2:
        sid = d['student'].id
        if sid not in students_map:
            students_map[sid] = {'student': d['student'], 'score1': None, 'result1': None}
        students_map[sid].update({'score2': d['total_score'], 'result2': d['result']})

    total_data = []
    for item in students_map.values():
        s1, s2 = item['score1'], item['score2']
        total = (s1 or 0) + (s2 or 0) if s1 is not None or s2 is not None else None
        progress = s2 - s1 if s1 is not None and s2 is not None else None
        item.update({'total_score': total, 'progress': progress, 'display_class': item['student'].school_class.name})
        total_data.append(item)
    
    total_data.sort(key=lambda x: (x['total_score'] is None, -x['total_score'] if x['total_score'] else 0))

    context = {
        'title': f'Рейтинг параллели: {parent_class.name} классы', 'parent_class': parent_class, 'quarter': quarter,
        'students_data_total': total_data, 'students_data_day1': data1, 'students_data_day2': data2,
        'table_header_day1': header1, 'table_header_day2': header2,
        'test_day1': test1, 'test_day2': test2, 'selected_gat_number': gat_number, 'gat_number_choices': GatTest.TEST_NUMBER_CHOICES
    }
    return render(request, 'results/combined_class_report.html', context)

@login_required
def compare_class_tests_view(request, test1_id, test2_id):
    test1 = get_object_or_404(GatTest, id=test1_id)
    test2 = get_object_or_404(GatTest, id=test2_id)

    data1, h1 = _get_data_for_test_obj(test1)
    data2, h2 = _get_data_for_test_obj(test2)

    map1 = {d['student'].id: d for d in data1}
    map2 = {d['student'].id: d for d in data2}
    
    all_students = set(map1.keys()) | set(map2.keys())
    students_objs = Student.objects.filter(id__in=all_students).select_related('school_class')
    
    comparison = []
    for s in students_objs:
        d1 = map1.get(s.id)
        d2 = map2.get(s.id)
        score1 = d1['total_score'] if d1 else '—'
        score2 = d2['total_score'] if d2 else '—'
        
        rank1 = data1.index(d1) + 1 if d1 else '—'
        rank2 = data2.index(d2) + 1 if d2 else '—'
        
        progress = None
        if isinstance(score1, int) and isinstance(score2, int):
            progress = score2 - score1

        comparison.append({
            'student': s, 'score1': score1, 'score2': score2,
            'rank1': rank1, 'rank2': rank2, 'progress': progress
        })

    context = {
        'title': 'Сравнение тестов', 'results': comparison, 'test1': test1, 'test2': test2,
        'students_data_1': data1, 'table_header_1': h1,
        'students_data_2': data2, 'table_header_2': h2
    }
    return render(request, 'results/comparison_detail.html', context)


# =============================================================================
# --- 4. АНАЛИЗ (ANALYSIS VIEW) ---
# =============================================================================

@login_required
def analysis_view(request):
    user = request.user
    form = StatisticsFilterForm(request.GET or None, user=user)
    
    context = {
        'title': 'Анализ успеваемости', 'form': form, 'has_results': False,
        'selected_class_ids_json': json.dumps(request.GET.getlist('school_classes')),
        'selected_subject_ids_json': json.dumps(request.GET.getlist('subjects')),
    }

    if form.is_valid():
        accessible_schools = get_accessible_schools(user)
        results_qs = StudentResult.objects.filter(student__school_class__school__in=accessible_schools)
        
        if form.cleaned_data['quarters']: results_qs = results_qs.filter(gat_test__quarter__in=form.cleaned_data['quarters'])
        if form.cleaned_data['schools']: results_qs = results_qs.filter(student__school_class__school__in=form.cleaned_data['schools'])
        # (добавить остальные фильтры из StatisticsFilterForm, если нужно)

        if results_qs.exists():
            context['has_results'] = True
            
            parallel_ids = set(results_qs.values_list('gat_test__school_class_id', flat=True))
            qc_qs = QuestionCount.objects.filter(school_class_id__in=parallel_ids)
            qc_map = defaultdict(dict)
            for qc in qc_qs:
                qc_map[qc.school_class_id][qc.subject_id] = qc.number_of_questions

            agg_data = defaultdict(lambda: defaultdict(lambda: {'correct': 0, 'total_possible': 0}))
            
            for result in results_qs.select_related('student__school_class', 'gat_test'):
                class_name = result.student.school_class.name
                parallel_id = result.gat_test.school_class_id 
                
                if isinstance(result.scores_by_subject, dict):
                    for subj_id_str, answers in result.scores_by_subject.items():
                        try:
                            subj_id = int(subj_id_str)
                            max_score = qc_map.get(parallel_id, {}).get(subj_id, 0)
                            
                            if max_score > 0 and isinstance(answers, dict):
                                correct = sum(1 for v in answers.values() if v is True)
                                agg_data[class_name][subj_id]['correct'] += correct
                                agg_data[class_name][subj_id]['total_possible'] += max_score
                        except: continue

            table_data = defaultdict(dict)
            subject_names = {s.id: s.name for s in Subject.objects.all()}
            
            for class_name, subjs in agg_data.items():
                for subj_id, scores in subjs.items():
                    if scores['total_possible'] > 0:
                        percent = round((scores['correct'] / scores['total_possible']) * 100, 1)
                        subj_name = subject_names.get(subj_id, f"Subj {subj_id}")
                        table_data[subj_name][class_name] = percent

            all_classes = sorted(agg_data.keys())
            sorted_subjects = sorted(table_data.keys())
            
            context['table_headers'] = all_classes
            context['table_data'] = table_data
            
            chart_datasets = []
            for cls in all_classes:
                data = [table_data.get(subj, {}).get(cls, 0) for subj in sorted_subjects]
                chart_datasets.append({'label': cls, 'data': data})
                
            context['chart_labels'] = json.dumps(sorted_subjects, ensure_ascii=False)
            context['chart_datasets'] = json.dumps(chart_datasets, ensure_ascii=False)

    return render(request, 'analysis.html', context)


# =============================================================================
# --- 5. ЭКСПОРТ (EXCEL/PDF) ---
# =============================================================================

@login_required
def export_detailed_results_excel(request, test_number):
    data = get_detailed_results_data(test_number, request.GET, request.user)
    students_data = data['students_data']
    header = data['table_header']

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="GAT-{test_number}_results.xlsx"'

    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"

    row1 = ["№", "ID", "ФИО", "Класс", "Школа"]
    for h in header:
        subj = h['subject'].abbreviation or h['subject'].name
        for i in range(1, h['questions_count'] + 1):
            row1.append(f"{subj}_{i}")
    row1.extend(["Общий балл", "Место"])
    ws.append(row1)

    for i, d in enumerate(students_data, 1):
        row = [i, d['student'].student_id, d['student'].full_name_ru, d['student'].school_class.name, d['student'].school_class.school.name]
        
        result = d.get('result')
        if result and isinstance(result.scores_by_subject, dict):
            for h in header:
                sid = str(h['subject'].id)
                answers = result.scores_by_subject.get(sid, {})
                for q in range(1, h['questions_count'] + 1):
                    val = answers.get(str(q), "")
                    row.append(1 if val is True else (0 if val is False else ""))
        else:
            total_q = sum(h['questions_count'] for h in header)
            row.extend([""] * total_q)

        row.append(d['total_score'])
        row.append(d['position'])
        ws.append(row)

    wb.save(response)
    return response

@login_required
def export_detailed_results_pdf(request, test_number):
    data = get_detailed_results_data(test_number, request.GET, request.user)
    
    context = {
        'title': f'GAT-{test_number} Report',
        'students_data': data['students_data'],
        'table_header': data['table_header'],
        'test_info': data['test'],
        'export_date': utils.get_current_date()
    }
    
    html = render_to_string('results/detailed_results_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="GAT-{test_number}.pdf"'
    HTML(string=html, base_url=request.build_absolute_uri()).write_pdf(response)
    return response