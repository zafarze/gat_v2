import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.utils import timezone

from ..models import Student, GatTest, StudentResult, Quarter, AcademicYear, Subject, QuestionCount
from .permissions import get_accessible_schools
from .. import utils

def _get_date_filters(request):
    """Определяет фильтр по дате на основе GET-параметров."""
    today = timezone.now().date()
    period = request.GET.get('period', 'year')
    selected_year_id = request.GET.get('year')
    start_date, end_date = None, None

    if selected_year_id:
        try:
            selected_year = AcademicYear.objects.get(pk=selected_year_id)
            start_date, end_date = selected_year.start_date, selected_year.end_date
            period = 'archive'
        except AcademicYear.DoesNotExist:
            period = 'all'
    elif period == 'quarter':
        current_quarter = Quarter.objects.filter(start_date__lte=today, end_date__gte=today).first()
        if current_quarter:
            start_date, end_date = current_quarter.start_date, current_quarter.end_date
    elif period == 'year':
        current_year = AcademicYear.objects.filter(start_date__lte=today, end_date__gte=today).first()
        if current_year:
            start_date, end_date = current_year.start_date, current_year.end_date

    return period, start_date, end_date

def _calculate_kpis(results_qs, accessible_schools):
    """Расчет KPI на основе отфильтрованного набора результатов."""
    if not results_qs.exists():
        return {
            'school_count': accessible_schools.count(),
            'student_count': 0,
            'subject_count': Subject.objects.count(),
            'test_count': 0,
        }

    distinct_student_ids = results_qs.values_list('student_id', flat=True).distinct()
    distinct_test_ids = results_qs.values_list('gat_test_id', flat=True).distinct()

    return {
        'school_count': accessible_schools.count(),
        'student_count': len(distinct_student_ids),
        'subject_count': Subject.objects.count(),
        'test_count': len(distinct_test_ids),
    }

def _get_performance_chart_data(user, base_qs):
    """Готовит данные для графика успеваемости (школы или классы)."""
    if not base_qs.exists():
        return json.dumps([]), json.dumps([])

    if user.is_staff or (hasattr(user, 'profile') and user.profile.role == 'EXPERT'):
        performance = base_qs.values('student__school_class__school__name').annotate(avg_score=Avg('total_score')).order_by('-avg_score')[:10]
        labels = [item['student__school_class__school__name'] for item in performance]
    else:
        performance = base_qs.values('student__school_class__name').annotate(avg_score=Avg('total_score')).order_by('-avg_score')[:10]
        labels = [item['student__school_class__name'] for item in performance]

    data = [round(item['avg_score'], 1) for item in performance]
    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

def _get_subject_chart_data(base_qs):
    """
    Готовит данные для графика предметов.
    ИСПРАВЛЕНО: Теперь поддерживает и списки [True, False], и словари {"1": True}.
    """
    subject_performance = defaultdict(lambda: {'correct': 0, 'total': 0})
    
    # Кэшируем названия предметов {id: 'Math', 'Math': 'Math'} для быстрого поиска
    all_subjects = Subject.objects.all()
    subject_map = {str(s.id): s.name for s in all_subjects}
    for s in all_subjects:
        subject_map[s.name] = s.name

    for result in base_qs.only('scores_by_subject'):
        if not result.scores_by_subject or not isinstance(result.scores_by_subject, dict):
            continue

        for subj_key, answers in result.scores_by_subject.items():
            subj_key_str = str(subj_key)
            
            # Определяем имя предмета (по ID или по названию)
            subject_name = subject_map.get(subj_key_str)
            if not subject_name:
                # Если ключа нет в базе, пропускаем или используем как есть
                subject_name = subj_key_str 

            correct = 0
            total = 0

            # ВАРИАНТ 1: Данные - это словарь {"1": True, "2": False}
            if isinstance(answers, dict):
                correct = sum(1 for v in answers.values() if v is True)
                total = len(answers)
            
            # ВАРИАНТ 2: Данные - это список [True, False, True]
            elif isinstance(answers, list):
                correct = sum(1 for v in answers if v is True)
                total = len(answers)

            if total > 0:
                subject_performance[subject_name]['correct'] += correct
                subject_performance[subject_name]['total'] += total

    if not subject_performance:
        return json.dumps([]), json.dumps([])

    final_data = []
    for name, stats in subject_performance.items():
        if stats['total'] > 0:
            avg = (stats['correct'] / stats['total']) * 100
            final_data.append({'name': name, 'avg': round(avg, 1)})

    # Сортируем: сначала высокие баллы
    final_data.sort(key=lambda x: x['avg'], reverse=True)
    final_data = final_data[:10] # Топ 10
    
    # Для графика лучше сортировать по возрастанию
    final_data.sort(key=lambda x: x['avg'])

    labels = [x['name'] for x in final_data]
    data = [x['avg'] for x in final_data]

    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

def _get_student_widgets_data(base_qs):
    """Готовит данные для виджетов студентов."""
    if not base_qs.exists():
        return [], []

    student_scores = base_qs.values('student').annotate(avg_score=Avg('total_score')).order_by('-avg_score')

    top_ids = [item['student'] for item in student_scores[:5]]
    # Берем худших с конца списка (самый низкий балл)
    worst_ids = [item['student'] for item in student_scores.order_by('avg_score')[:5]]

    all_ids = list(set(top_ids + worst_ids))
    students_map = {s.id: s for s in Student.objects.filter(id__in=all_ids).select_related('school_class')}
    scores_map = {item['student']: item['avg_score'] for item in student_scores if item['student'] in all_ids}

    top_students = []
    for sid in top_ids:
        st = students_map.get(sid)
        if st:
            top_students.append({'student': st, 'avg_score': round(scores_map[sid], 1)})

    worst_students = []
    for sid in worst_ids:
        st = students_map.get(sid)
        if st:
            worst_students.append({'student': st, 'avg_score': round(scores_map[sid], 1)})

    return top_students, worst_students

@login_required
def dashboard_view(request):
    user = request.user
    period, start_date, end_date = _get_date_filters(request)
    accessible_schools = get_accessible_schools(user)

    # Базовый QuerySet
    base_results_qs = StudentResult.objects.filter(student__school_class__school__in=accessible_schools)
    if start_date and end_date:
        base_results_qs = base_results_qs.filter(gat_test__test_date__range=(start_date, end_date))

    # KPI
    kpis = _calculate_kpis(base_results_qs, accessible_schools)

    # Графики и списки
    school_labels, school_data = _get_performance_chart_data(user, base_results_qs)
    subject_labels, subject_data = _get_subject_chart_data(base_results_qs)
    top_students, worst_students = _get_student_widgets_data(base_results_qs)
    
    recent_tests = GatTest.objects.filter(school__in=accessible_schools).select_related('school', 'school_class').order_by('-test_date')[:5]

    # Распределение (Пончик)
    grades = []
    # Предзагрузка макс. баллов для оптимизации
    test_max_score_map = {} 
    
    if base_results_qs.exists():
        test_ids = base_results_qs.values_list('gat_test_id', flat=True).distinct()
        tests = GatTest.objects.filter(id__in=test_ids).prefetch_related('subjects', 'school_class')
        
        # Собираем ID классов/параллелей
        class_ids = set()
        for t in tests:
            if t.school_class:
                class_ids.add(t.school_class.parent_id or t.school_class.id)
        
        # Загружаем кол-во вопросов
        qc_map = defaultdict(dict) # {class_id: {subject_id: count}}
        q_counts = QuestionCount.objects.filter(school_class_id__in=class_ids)
        for qc in q_counts:
            qc_map[qc.school_class_id][qc.subject_id] = qc.number_of_questions
            
        # Считаем макс балл для каждого теста
        for t in tests:
            if not t.school_class: continue
            cid = t.school_class.parent_id or t.school_class.id
            m_score = 0
            for subj in t.subjects.all():
                m_score += qc_map.get(cid, {}).get(subj.id, 0)
            test_max_score_map[t.id] = m_score

        # Считаем оценки
        for res in base_results_qs.only('total_score', 'gat_test_id'):
            max_s = test_max_score_map.get(res.gat_test_id, 0)
            if max_s > 0:
                perc = (res.total_score / max_s) * 100
                grades.append(utils.calculate_grade_from_percentage(perc))

    dist_labels = ["Отлично (8-10)", "Хорошо (6-7)", "Удовл. (4-5)", "Неуд. (<4)"]
    dist_data = [
        sum(1 for g in grades if g >= 8),
        sum(1 for g in grades if 6 <= g <= 7),
        sum(1 for g in grades if 4 <= g <= 5),
        sum(1 for g in grades if g < 4)
    ]

    context = {
        'title': 'Панель управления',
        'selected_period': period,
        'school_chart_labels': school_labels, 
        'school_chart_data': school_data,
        'subject_chart_labels': subject_labels, 
        'subject_chart_data': subject_data,
        'top_students': top_students, 
        'worst_students': worst_students,
        'recent_tests': recent_tests,
        'distribution_chart_labels': json.dumps(dist_labels, ensure_ascii=False),
        'distribution_chart_data': json.dumps(dist_data),
        **kpis
    }
    return render(request, 'dashboard.html', context)