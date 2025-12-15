# D:\New_GAT\core\views\dashboard.py (Полная исправленная версия)

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Sum, Count, Q
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

# ✨ ИЗМЕНЕНИЕ 1: Функция для KPI теперь работает на основе реальных результатов
def _calculate_kpis(results_qs, accessible_schools):
    """
    Расчет KPI на основе отфильтрованного набора результатов (StudentResult).
    Это гарантирует, что KPI соответствуют данным на графиках.
    """
    if not results_qs.exists():
        return {
            'school_count': accessible_schools.count(),
            'student_count': 0,
            'subject_count': 0, # Предметы теперь глобальные, считаем все
            'test_count': 0,
        }

    # Считаем уникальные ID из queryset'а, чтобы избежать лишних запросов к БД
    distinct_student_ids = results_qs.values_list('student_id', flat=True).distinct()
    distinct_test_ids = results_qs.values_list('gat_test_id', flat=True).distinct()

    # Считаем все предметы, так как они глобальные
    subject_count = Subject.objects.count()

    return {
        'school_count': accessible_schools.count(),
        'student_count': len(distinct_student_ids),
        'subject_count': subject_count,
        'test_count': len(distinct_test_ids),
    }


def _get_performance_chart_data(user, base_qs):
    """Готовит данные для графика успеваемости (школы или классы)."""
    if not base_qs.exists():
        return json.dumps([]), json.dumps([])

    if user.is_staff or (hasattr(user, 'profile') and user.profile.role == 'EXPERT'):
        # Для админов/экспертов - рейтинг школ
        performance = base_qs.values('student__school_class__school__name').annotate(avg_score=Avg('total_score')).order_by('-avg_score')[:10]
        labels = [item['student__school_class__school__name'] for item in performance]
    else:
        # Для директоров/учителей - рейтинг классов в их школах
        performance = base_qs.values('student__school_class__name').annotate(avg_score=Avg('total_score')).order_by('-avg_score')[:10]
        labels = [item['student__school_class__name'] for item in performance]

    data = [round(item['avg_score'], 1) for item in performance]
    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

def _get_subject_chart_data(base_qs):
    """
    Готовит данные для графика предметов из JSON-поля `scores_by_subject`.
    ИСПРАВЛЕННАЯ ВЕРСИЯ: Читает формат {"subject_id": {"1": True, "2": False}}
    """
    subject_performance = defaultdict(lambda: {'correct': 0, 'total': 0})

    # Оптимизация: загружаем из БД только поле scores_by_subject
    for result in base_qs.only('scores_by_subject'):
        if isinstance(result.scores_by_subject, dict):
            
            # Итерируем по { "id_предмета": {"1": True, "2": False} }
            for subject_id_str, answers_dict in result.scores_by_subject.items():
                
                # ИСПРАВЛЕНИЕ: Проверяем, что 'answers' - это СЛОВАРЬ (dict), а не список
                if answers_dict and isinstance(answers_dict, dict): 
                    try:
                        subject_id = int(subject_id_str)
                        
                        # ИСПРАВЛЕНИЕ: Считаем True в словаре ответов
                        correct_count = sum(1 for was_correct in answers_dict.values() if was_correct is True)
                        
                        # ИСПРАВЛЕНИЕ: Общее число - это кол-во ключей в словаре
                        total_count = len(answers_dict)

                        subject_performance[subject_id]['correct'] += correct_count
                        subject_performance[subject_id]['total'] += total_count
                        
                    except (ValueError, TypeError):
                        continue # Пропускаем некорректные ключи или значения

    if not subject_performance:
        return json.dumps([]), json.dumps([])

    subject_map = {s.id: s.name for s in Subject.objects.filter(id__in=subject_performance.keys())}

    subject_avg_scores = []
    for subject_id, data in subject_performance.items():
        if data['total'] > 0 and subject_id in subject_map:
            avg_percent = (data['correct'] / data['total']) * 100
            subject_avg_scores.append({'name': subject_map[subject_id], 'avg_score': round(avg_percent, 1)})

    # Сортируем и берем топ-10
    top_subjects = sorted(subject_avg_scores, key=lambda x: x['avg_score'], reverse=True)[:10]
    
    # Сортируем топ-10 по возрастанию для лучшего отображения на графике
    top_subjects.sort(key=lambda x: x['avg_score']) 

    labels = [s['name'] for s in top_subjects]
    data = [s['avg_score'] for s in top_subjects]

    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

    subject_map = {s.id: s.name for s in Subject.objects.filter(id__in=subject_performance.keys())}

    subject_avg_scores = []
    for subject_id, data in subject_performance.items():
        if data['total'] > 0 and subject_id in subject_map:
            avg_percent = (data['correct'] / data['total']) * 100
            subject_avg_scores.append({'name': subject_map[subject_id], 'avg_score': round(avg_percent, 1)})

    # Сортируем и берем топ-10
    top_subjects = sorted(subject_avg_scores, key=lambda x: x['avg_score'], reverse=True)[:10]
    
    # Сортируем топ-10 по возрастанию для лучшего отображения на графике
    top_subjects.sort(key=lambda x: x['avg_score']) 

    labels = [s['name'] for s in top_subjects]
    data = [s['avg_score'] for s in top_subjects]

    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

    subject_map = {s.id: s.name for s in Subject.objects.filter(id__in=subject_performance.keys())}

    subject_avg_scores = []
    for subject_id, data in subject_performance.items():
        if data['total'] > 0 and subject_id in subject_map:
            avg_percent = (data['correct'] / data['total']) * 100
            subject_avg_scores.append({'name': subject_map[subject_id], 'avg_score': round(avg_percent, 1)})

    top_subjects = sorted(subject_avg_scores, key=lambda x: x['avg_score'], reverse=True)[:10]
    labels = [s['name'] for s in top_subjects]
    data = [s['avg_score'] for s in top_subjects]

    return json.dumps(labels, ensure_ascii=False), json.dumps(data)

def _get_student_widgets_data(base_qs):
    """Готовит данные для виджетов лучших и худших студентов."""
    if not base_qs.exists():
        return [], []

    student_scores = base_qs.values('student').annotate(avg_score=Avg('total_score')).order_by('-avg_score')

    top_student_ids = [item['student'] for item in student_scores[:5]]
    # Исправлено: берем с конца отсортированного списка для худших
    worst_student_ids = [item['student'] for item in student_scores.order_by('avg_score')[:5]]

    all_ids = list(set(top_student_ids + worst_student_ids))
    students_map = {s.id: s for s in Student.objects.filter(id__in=all_ids).select_related('school_class')}
    scores_map = {item['student']: item['avg_score'] for item in student_scores if item['student'] in all_ids}

    top_students = [{'student': students_map.get(sid), 'avg_score': round(scores_map.get(sid), 1)} for sid in top_student_ids if students_map.get(sid)]
    worst_students = [{'student': students_map.get(sid), 'avg_score': round(scores_map.get(sid), 1)} for sid in worst_student_ids if students_map.get(sid)]
    worst_students.sort(key=lambda x: x['avg_score']) # Сортируем худших по возрастанию балла

    return top_students, worst_students

@login_required
def dashboard_view(request):
    user = request.user
    period, start_date, end_date = _get_date_filters(request)
    accessible_schools = get_accessible_schools(user)

    # Сначала формируем базовый queryset для ВСЕХ расчетов
    base_results_qs = StudentResult.objects.filter(student__school_class__school__in=accessible_schools)
    if start_date and end_date:
        base_results_qs = base_results_qs.filter(gat_test__test_date__range=(start_date, end_date))

    # ✨ ИЗМЕНЕНИЕ 2: Передаем готовый queryset в функцию расчета KPI
    kpis = _calculate_kpis(base_results_qs, accessible_schools)

    # --- Все остальные расчеты используют тот же самый base_results_qs ---
    school_chart_labels, school_chart_data = _get_performance_chart_data(user, base_results_qs)
    subject_chart_labels, subject_chart_data = _get_subject_chart_data(base_results_qs)
    top_students, worst_students = _get_student_widgets_data(base_results_qs)
    # Оптимизация: загружаем связанные объекты для недавних тестов
    recent_tests = GatTest.objects.filter(school__in=accessible_schools).select_related('school', 'school_class').order_by('-test_date')[:5]

    # --- ИСПРАВЛЕННАЯ ЛОГИКА ДЛЯ ГРАФИКА РАСПРЕДЕЛЕНИЯ УСПЕВАЕМОСТИ ---
    grades = []
    test_max_score_map = {} # Словарь для хранения максимального балла {gat_test_id: max_score}

    if base_results_qs.exists():
        # 1. Получаем уникальные ID тестов из результатов
        test_ids = base_results_qs.values_list('gat_test_id', flat=True).distinct()

        # 2. Загружаем объекты GatTest с их предметами и классами (параллелями)
        tests_in_results = GatTest.objects.filter(id__in=test_ids).prefetch_related('subjects').select_related('school_class')

        # 3. Получаем все нужные QuestionCounts одним запросом
        #    Нам нужны QuestionCounts для параллелей (parent_class) этих тестов
        parent_class_ids = set()
        for t in tests_in_results:
            if t.school_class:
                 # Используем ID параллели или ID самого класса, если он - параллель
                 parent_class_ids.add(t.school_class.parent_id if t.school_class.parent_id else t.school_class.id)

        # Убираем None, если у какого-то класса не было родителя или самого себя (маловероятно)
        parent_class_ids.discard(None)

        all_relevant_q_counts_qs = QuestionCount.objects.filter(school_class_id__in=parent_class_ids)

        # 4. Создаем удобную структуру для QuestionCounts: {parent_class_id: {subject_id: count}}
        q_counts_map = defaultdict(dict)
        for qc in all_relevant_q_counts_qs:
            q_counts_map[qc.school_class_id][qc.subject_id] = qc.number_of_questions

        # 5. Рассчитываем правильный максимальный балл для каждого теста
        for test in tests_in_results:
            max_score = 0
            # Определяем ID параллели для этого теста
            parent_class_id = None
            if test.school_class:
                parent_class_id = test.school_class.parent_id if test.school_class.parent_id else test.school_class.id

            if parent_class_id:
                counts_for_parallel = q_counts_map.get(parent_class_id, {})
                # Суммируем кол-во вопросов только по тем предметам, которые есть В ЭТОМ ТЕСТЕ
                for subject in test.subjects.all(): # Итерация по предметам теста
                    max_score += counts_for_parallel.get(subject.id, 0) # Берем кол-во из карты

            test_max_score_map[test.id] = max_score

        # 6. Рассчитываем оценки для каждого результата
        # Оптимизация: предзагружаем gat_test_id, чтобы не делать лишних запросов в цикле
        for res in base_results_qs.only('total_score', 'gat_test_id'):
            # Используем правильный максимальный балл для этого теста
            total_possible_score = test_max_score_map.get(res.gat_test_id, 0)
            if total_possible_score > 0:
                percentage = (res.total_score / total_possible_score) * 100
                # Используем вашу утилиту для получения оценки
                grades.append(utils.calculate_grade_from_percentage(percentage))
            # else: # Если макс. балл = 0, оценку не считаем или ставим минимальную
            #     grades.append(1) # Например

    # 7. Считаем распределение (этот блок остается без изменений)
    distribution_labels = ["Отлично (8-10)", "Хорошо (6-7)", "Удовл. (4-5)", "Неуд. (<4)"]
    distribution_data = [
        sum(1 for g in grades if g >= 8),
        sum(1 for g in grades if 6 <= g <= 7),
        sum(1 for g in grades if 4 <= g <= 5),
        sum(1 for g in grades if g < 4)
    ]
    # --- КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ ---

    context = {
        'title': 'Панель управления',
        'selected_period': period,
        'school_chart_labels': school_chart_labels, 'school_chart_data': school_chart_data,
        'subject_chart_labels': subject_chart_labels, 'subject_chart_data': subject_chart_data,
        'top_students': top_students, 'worst_students': worst_students,
        'recent_tests': recent_tests,
        # Передаем обновленные данные для диаграммы распределения
        'distribution_chart_labels': json.dumps(distribution_labels, ensure_ascii=False),
        'distribution_chart_data': json.dumps(distribution_data),
        **kpis
    }
    return render(request, 'dashboard.html', context)