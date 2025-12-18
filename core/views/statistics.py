# D:\New_GAT\core\views\statistics.py

import json
from collections import defaultdict
from typing import Dict, List, Tuple, Any

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.core.cache import cache

# Импорты из вашего проекта
from ..models import StudentResult, Subject, SchoolClass, QuestionCount
from ..forms import StatisticsFilterForm
from .. import utils
from .permissions import get_accessible_schools
from accounts.models import UserProfile


def _build_question_counts_map(subjects: QuerySet) -> Dict[int, Dict[int, int]]:
    """
    Создает карту максимального количества вопросов.
    Кэширование результатов для улучшения производительности.
    """
    cache_key = f"question_counts_{hash(tuple(subjects.values_list('id', flat=True)))}"
    cached_result = cache.get(cache_key)
    
    if cached_result is not None:
        return cached_result
    
    q_counts_qs = QuestionCount.objects.filter(
        subject__in=subjects
    ).select_related('subject', 'school_class')
    
    q_counts_map = defaultdict(dict)
    for qc in q_counts_qs:
        parallel_id = qc.school_class.parent_id or qc.school_class.id
        q_counts_map[parallel_id][qc.subject.id] = qc.number_of_questions
    
    cache.set(cache_key, dict(q_counts_map), timeout=300)  # 5 минут
    return dict(q_counts_map)


def _calculate_student_score(answers: Any) -> int:
    """Безопасный расчет баллов студента из JSON поля."""
    if not isinstance(answers, dict):
        return 0
    return sum(1 for v in answers.values() if v is True)


def _get_subject_name_map(subject_ids: List[int]) -> Dict[int, str]:
    """Получение карты имен предметов для избежания запросов в цикле."""
    return {
        subj.id: subj.name 
        for subj in Subject.objects.filter(id__in=subject_ids)
    }


def _prepare_chart_data(grade_distribution: Dict[int, int]) -> Tuple[List[int], List[int]]:
    """Подготовка данных для Chart.js с сортировкой от 1 до 10."""
    sorted_grades = sorted(range(1, 11))
    chart_data = [grade_distribution.get(grade, 0) for grade in sorted_grades]
    return sorted_grades, chart_data


def _process_grade_distribution_report(
    report: Dict[str, Dict[str, Any]], 
    grade_range: range
) -> Dict[str, Dict[str, Any]]:
    """Обработка и агрегация данных для отчета по предметам."""
    processed_report = {}
    
    for subject_name, class_data in report.items():
        processed_report[subject_name] = {}
        total_grades_list = []
        total_correct_subj = 0
        total_possible_subj = 0
        
        for class_name, data in class_data.items():
            grades_list = data['grades_list']
            correct_class = data['correct_total']
            possible_class = data['possible_total']
            
            # Подсчет оценок для класса
            grade_counts = {g: grades_list.count(g) for g in grade_range}
            
            processed_report[subject_name][class_name] = {
                'grades': grade_counts,
                'average_score': round((correct_class / possible_class) * 100, 1) 
                if possible_class > 0 else 0
            }
            
            total_grades_list.extend(grades_list)
            total_correct_subj += correct_class
            total_possible_subj += possible_class
        
        # Итог по предмету
        if total_grades_list:
            processed_report[subject_name]['Итог'] = {
                'grades': {g: total_grades_list.count(g) for g in grade_range},
                'average_score': round((total_correct_subj / total_possible_subj) * 100, 1) 
                if total_possible_subj > 0 else 0
            }
    
    return processed_report


@login_required
def statistics_view(request):
    """Отображает страницу 'Статистика' с оптимизированными запросами."""
    user = request.user
    form = StatisticsFilterForm(request.GET or None, user=user)
    
    # Базовый контекст
    context = {
        'title': 'Статистика результатов GAT тестов',
        'form': form,
        'has_results': False,
        'selected_quarter_ids': request.GET.getlist('quarters'),
        'selected_school_ids': request.GET.getlist('schools'),
        'selected_class_ids': request.GET.getlist('school_classes'),
        'selected_class_ids_json': json.dumps(request.GET.getlist('school_classes')),
        'selected_subject_ids': request.GET.getlist('subjects'),
        'selected_subject_ids_json': json.dumps(request.GET.getlist('subjects')),
    }
    
    if not form.is_valid():
        return render(request, 'statistics/statistics.html', context)
    
    # Получение отфильтрованных данных
    schools = form.cleaned_data.get('schools')
    school_classes = form.cleaned_data.get('school_classes')
    subjects = form.cleaned_data.get('subjects')
    quarters = form.cleaned_data.get('quarters')
    
    # Если предметы не выбраны - используем все доступные
    if not subjects:
        subjects = Subject.objects.all()
    
    # Базовый запрос с оптимизацией
    results_qs = StudentResult.objects.select_related(
        'student', 
        'gat_test', 
        'student__school_class',
        'student__school_class__school'
    ).filter(gat_test__quarter__in=quarters)
    
    # Применение фильтров
    if schools:
        results_qs = results_qs.filter(gat_test__school_class__school__in=schools)
    if school_classes:
        results_qs = results_qs.filter(gat_test__school_class__in=school_classes)
    
    has_results = results_qs.exists()
    context['has_results'] = has_results
    
    if not has_results:
        return render(request, 'statistics/statistics.html', context)
    
    # Подготовка вспомогательных структур
    subject_ids = [s.id for s in subjects]
    subject_name_map = _get_subject_name_map(subject_ids)
    q_counts_map = _build_question_counts_map(subjects)
    
    # Инициализация структур данных
    grade_distribution = defaultdict(int)
    student_performance = defaultdict(lambda: {'total_score': 0, 'total_possible': 0})
    grade_distribution_report = defaultdict(
        lambda: defaultdict(lambda: {'grades_list': [], 'correct_total': 0, 'possible_total': 0})
    )
    
    # Основной цикл обработки
    for res in results_qs.iterator(chunk_size=1000):  # Итерация с чанками для больших данных
        student = res.student
        cls = student.school_class
        parallel_id = cls.parent_id or cls.id
        
        if not isinstance(res.scores_by_subject, dict):
            continue
            
        for subject_id_str, answers in res.scores_by_subject.items():
            try:
                subject_id = int(subject_id_str)
            except (ValueError, TypeError):
                continue
                
            # Пропускаем если предмет не в выбранных
            if subject_id not in subject_ids:
                continue
            
            # Получаем максимальный балл
            max_score = q_counts_map.get(parallel_id, {}).get(subject_id, 0)
            if max_score == 0:
                continue
            
            # Расчет балла студента
            student_score = _calculate_student_score(answers)
            percent = (student_score / max_score) * 100
            grade = utils.calculate_grade_from_percentage(percent)
            
            # Обновление структур данных
            grade_distribution[grade] += 1
            
            student_key = student.id
            student_performance[student_key]['total_score'] += student_score
            student_performance[student_key]['total_possible'] += max_score
            
            # Добавление в отчет по предметам
            subject_name = subject_name_map.get(subject_id, f"Предмет {subject_id}")
            class_name = cls.name
            
            report_entry = grade_distribution_report[subject_name][class_name]
            report_entry['grades_list'].append(grade)
            report_entry['correct_total'] += student_score
            report_entry['possible_total'] += max_score
    
    # Расчет KPI
    total_correct_all = sum(d['total_score'] for d in student_performance.values())
    total_possible_all = sum(d['total_possible'] for d in student_performance.values())
    
    context['average_score'] = round((total_correct_all / total_possible_all) * 100, 1) \
        if total_possible_all > 0 else 0
    context['total_students'] = len(student_performance)
    
    # Подготовка данных для графиков
    grade_range = range(10, 0, -1)
    context['grade_range'] = grade_range
    
    # График распределения оценок
    context['grade_labels'] = list(grade_distribution.keys())
    context['grade_data'] = list(grade_distribution.values())
    
    chart_labels, chart_data = _prepare_chart_data(grade_distribution)
    context['chart_labels'] = chart_labels
    context['chart_data'] = chart_data
    
    # Отчет по предметам
    processed_report = _process_grade_distribution_report(
        grade_distribution_report, 
        grade_range
    )
    context['grade_distribution_report'] = processed_report
    
    # График по предметам (топ предметы)
    subject_perf_data = []
    subject_perf_labels = []
    
    for subject_name, data in processed_report.items():
        if 'Итог' in data:
            subject_perf_labels.append(subject_name)
            subject_perf_data.append(data['Итог']['average_score'])
    
    context['subject_perf_labels'] = json.dumps(subject_perf_labels, ensure_ascii=False)
    context['subject_perf_data'] = json.dumps(subject_perf_data)
    context['subject_perf_count'] = len(subject_perf_labels)
    
    return render(request, 'statistics/statistics.html', context)