# D:\New_GAT\core\views\statistics.py (Полная исправленная версия)

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Avg, Sum, Count, Q

# Импорты из вашего проекта
from ..models import StudentResult, Subject, SchoolClass, GatTest, QuestionCount # Добавлен QuestionCount
from ..forms import StatisticsFilterForm
from .. import utils
from .permissions import get_accessible_schools
from accounts.models import UserProfile

@login_required
def statistics_view(request):
    """
    Отображает страницу 'Статистика' со всеми KPI, графиками и таблицами,
    используя новую панель фильтров и учитывая права доступа Эксперта.
    Исправлен расчет процентов и оценок.
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    form = StatisticsFilterForm(request.GET or None, user=user)

    # --- Получение ID из GET для JS и начальной отрисовки ---
    selected_quarter_ids_str = request.GET.getlist('quarters')
    selected_school_ids_str = request.GET.getlist('schools')
    selected_class_ids_str = request.GET.getlist('school_classes')
    selected_subject_ids_str = request.GET.getlist('subjects')

    context = {
        'title': 'Статистика результатов GAT тестов',
        'form': form,
        'has_results': False,
        'selected_quarter_ids': selected_quarter_ids_str,
        'selected_school_ids': selected_school_ids_str,
        'selected_class_ids': selected_class_ids_str,
        'selected_class_ids_json': json.dumps(selected_class_ids_str),
        'selected_subject_ids_json': json.dumps(selected_subject_ids_str),
        # Инициализация
        'total_tests_taken': 0, 'top_subject': None, 'bottom_subject': None, 'average_score': 0,
        'school_summary_report': None, 'grade_range': range(10, 0, -1), 'grade_distribution_report': {},
        'subject_perf_labels': [], 'subject_perf_data': [], 'subject_perf_count': 0,
    }

    # --- Группировка классов для фильтра (без изменений) ---
    grouped_classes = defaultdict(list)
    if selected_school_ids_str:
        # ... (код группировки классов остается таким же) ...
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
        except ValueError:
            pass

    final_grouped_classes = {}
    sorted_group_items = sorted(
        grouped_classes.items(),
        key=lambda item: (not item[0].endswith("(Параллель)"), item[0])
    )
    for group_name, classes_in_group in sorted_group_items:
        classes_in_group.sort(key=lambda x: x.name)
        final_grouped_classes[group_name] = classes_in_group

    context['grouped_classes'] = final_grouped_classes
    # --- Конец группировки ---

    if form.is_valid():
        selected_quarters = form.cleaned_data['quarters']
        selected_schools = form.cleaned_data['schools']
        selected_classes_qs = form.cleaned_data['school_classes']
        selected_test_numbers = form.cleaned_data['test_numbers']
        selected_days = form.cleaned_data['days']
        selected_subjects_qs = form.cleaned_data['subjects']

        # --- Определение ID классов (включая дочерние) ---
        selected_class_ids_list = list(selected_classes_qs.values_list('id', flat=True))
        parent_class_ids = selected_classes_qs.filter(parent__isnull=True).values_list('id', flat=True)
        if parent_class_ids:
            child_class_ids = list(SchoolClass.objects.filter(parent_id__in=parent_class_ids).values_list('id', flat=True))
            selected_class_ids_list.extend(child_class_ids)
        final_class_ids = set(selected_class_ids_list)

        # --- Базовый QuerySet и Фильтрация (основная) ---
        accessible_schools = get_accessible_schools(user)
        results_qs = StudentResult.objects.filter(
            student__school_class__school__in=accessible_schools
        ).select_related('student__school_class__school', 'student__school_class__parent', 'gat_test__quarter__year', 'gat_test__school_class') # Добавили prefetch

        if selected_quarters: results_qs = results_qs.filter(gat_test__quarter__in=selected_quarters)
        if selected_schools: results_qs = results_qs.filter(student__school_class__school__in=selected_schools)
        if final_class_ids: results_qs = results_qs.filter(student__school_class_id__in=final_class_ids)
        if selected_test_numbers: results_qs = results_qs.filter(gat_test__test_number__in=selected_test_numbers)
        if selected_days: results_qs = results_qs.filter(gat_test__day__in=selected_days)

        # --- Фильтрация по ПРЕДМЕТАМ с учетом Эксперта (без изменений) ---
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
        else:
            accessible_subjects_qs = selected_subjects_qs

        if accessible_subjects_qs.exists():
            subject_id_keys_to_filter = [str(s.id) for s in accessible_subjects_qs]
            results_qs = results_qs.filter(scores_by_subject__has_any_keys=subject_id_keys_to_filter)
        elif is_expert:
             results_qs = results_qs.none()

        # --- ✨ НАЧАЛО ИСПРАВЛЕНИЙ В РАСЧЕТАХ ✨ ---
        if results_qs.exists():
            context['has_results'] = True
            context['total_tests_taken'] = results_qs.count()

            # 1. Определяем финальный список предметов для анализа
            final_subjects_for_analysis = Subject.objects.none()
            if accessible_subjects_qs.exists():
                final_subjects_for_analysis = accessible_subjects_qs # Используем результат фильтрации
            elif not is_expert: # Если Админ/Директор и предметы не выбраны, берем все из результатов
                all_subject_ids_in_results = set()
                # Оптимизация: используем values_list для получения словарей
                scores_list = results_qs.values_list('scores_by_subject', flat=True)
                for scores_dict in scores_list:
                    if isinstance(scores_dict, dict):
                        all_subject_ids_in_results.update(int(sid) for sid in scores_dict.keys())
                final_subjects_for_analysis = Subject.objects.filter(id__in=all_subject_ids_in_results)
            # Если is_expert и accessible_subjects_qs пуст, то final_subjects_for_analysis останется .none()

            # Если предметов для анализа нет, выходим
            if not final_subjects_for_analysis.exists():
                context['has_results'] = False # Сбрасываем флаг
            else:
                subject_map = {s.id: s.name for s in final_subjects_for_analysis}
                allowed_subject_ids_int = set(subject_map.keys())

                # 2. Загружаем QuestionCounts эффективно
                # Находим все уникальные ID параллелей из результатов
                parallel_ids_in_results = set()
                class_ids_in_results = results_qs.values_list('student__school_class_id', flat=True).distinct()
                classes_info = SchoolClass.objects.filter(id__in=class_ids_in_results).select_related('parent')
                class_to_parallel_map = {}
                for cls in classes_info:
                    p_id = cls.parent_id if cls.parent_id else cls.id
                    parallel_ids_in_results.add(p_id)
                    class_to_parallel_map[cls.id] = p_id

                # Загружаем QuestionCounts для этих параллелей и нужных предметов
                q_counts_qs = QuestionCount.objects.filter(
                    school_class_id__in=parallel_ids_in_results,
                    subject_id__in=allowed_subject_ids_int # Только нужные предметы
                )
                # Создаем карту: {parallel_id: {subject_id: count}}
                q_counts_map = defaultdict(dict)
                for qc in q_counts_qs:
                    q_counts_map[qc.school_class_id][qc.subject_id] = qc.number_of_questions

                # 3. Инициализация словарей для расчетов
                subject_performance = defaultdict(lambda: {'correct': 0, 'total_possible': 0}) # total -> total_possible
                school_summary_grades = defaultdict(int)
                grade_distribution_report = defaultdict(lambda: defaultdict(lambda: {'grades_list': [], 'percentage_list': [], 'correct_total': 0, 'possible_total': 0}))
                total_overall_correct, total_overall_possible = 0, 0

                # 4. Основной цикл обработки результатов
                for result in results_qs:
                    class_name = result.student.school_class.name
                    # Находим ID параллели для текущего ученика
                    current_parallel_id = class_to_parallel_map.get(result.student.school_class_id)
                    total_correct_student, total_possible_student_test = 0, 0 # Макс. балл для ЭТОГО теста

                    if not isinstance(result.scores_by_subject, dict) or not current_parallel_id: continue

                    # Итерация по предметам, которые ДОЛЖНЫ БЫТЬ в анализе
                    for subject_id in allowed_subject_ids_int:
                        subject_id_str = str(subject_id)
                        subject_name = subject_map[subject_id]
                        # Получаем кол-во вопросов для этого предмета и параллели
                        q_count_for_subject = q_counts_map.get(current_parallel_id, {}).get(subject_id, 0)

                        # Получаем ответы ученика (может быть None)
                        answers = result.scores_by_subject.get(subject_id_str) # answers is {'1': True, ...} or None

                        correct = 0
                        if isinstance(answers, dict):
                            correct = sum(1 for answer in answers.values() if answer is True)

                        # Обновляем общую статистику по предмету
                        subject_performance[subject_id]['correct'] += correct
                        subject_performance[subject_id]['total_possible'] += q_count_for_subject # Суммируем макс. балл

                        # Обновляем балл ученика для этого теста
                        total_correct_student += correct
                        total_possible_student_test += q_count_for_subject # Суммируем макс. балл для теста

                        # Расчеты для таблицы распределения оценок
                        if q_count_for_subject > 0:
                            # Процент считаем от МАКСИМАЛЬНО возможного балла
                            percentage = (correct / q_count_for_subject) * 100
                            grade = utils.calculate_grade_from_percentage(percentage)
                            grade_distribution_report[subject_name][class_name]['grades_list'].append(grade)
                            grade_distribution_report[subject_name][class_name]['percentage_list'].append(percentage)
                            # Накапливаем для расчета среднего балла в таблице
                            grade_distribution_report[subject_name][class_name]['correct_total'] += correct
                            grade_distribution_report[subject_name][class_name]['possible_total'] += q_count_for_subject


                    # Расчет общей оценки ученика за тест (относительно макс. балла теста)
                    if total_possible_student_test > 0:
                        overall_percentage = (total_correct_student / total_possible_student_test) * 100
                        overall_grade = utils.calculate_grade_from_percentage(overall_percentage)
                        school_summary_grades[overall_grade] += 1
                        total_overall_correct += total_correct_student
                        total_overall_possible += total_possible_student_test

                # 5. Обработка данных для KPI и графика успеваемости по предметам
                subject_averages = []
                for subject_id in allowed_subject_ids_int:
                    data = subject_performance[subject_id]
                    # Процент считаем от total_possible (макс. балла)
                    if data['total_possible'] > 0:
                        avg_percent = (data['correct'] / data['total_possible']) * 100
                        subject_averages.append({'id': subject_id, 'name': subject_map[subject_id], 'percentage': round(avg_percent, 1)})

                if subject_averages:
                    subject_averages.sort(key=lambda x: x['percentage'])
                    context['top_subject'] = subject_averages[-1]
                    context['bottom_subject'] = subject_averages[0]
                    subject_averages.reverse()
                    context['subject_perf_labels'] = [s['name'] for s in subject_averages]
                    context['subject_perf_data'] = [s['percentage'] for s in subject_averages]
                    context['subject_perf_count'] = len(subject_averages)

                # Общий средний балл (в процентах от макс. возможного)
                context['average_score'] = round((total_overall_correct / total_overall_possible) * 100, 1) if total_overall_possible > 0 else 0
                context['school_summary_report'] = {'grades': school_summary_grades, 'average_score': context['average_score']}

                # 6. Обработка данных для "Отчета по успеваемости"
                processed_grade_dist_report = {}
                for subject_name, class_data in grade_distribution_report.items():
                    processed_grade_dist_report[subject_name] = {}
                    total_grades_list, total_correct_subj, total_possible_subj = [], 0, 0

                    for class_name, data in class_data.items():
                        grades_list = data['grades_list']
                        correct_class, possible_class = data['correct_total'], data['possible_total']
                        processed_grade_dist_report[subject_name][class_name] = {
                            'grades': {grade: grades_list.count(grade) for grade in context['grade_range']},
                            # Средний балл считаем от МАКСИМАЛЬНОГО
                            'average_score': round((correct_class / possible_class) * 100, 1) if possible_class > 0 else 0
                        }
                        total_grades_list.extend(grades_list)
                        total_correct_subj += correct_class
                        total_possible_subj += possible_class

                    processed_grade_dist_report[subject_name]['Итог'] = {
                        'grades': {grade: total_grades_list.count(grade) for grade in context['grade_range']},
                        # Средний балл считаем от МАКСИМАЛЬНОГО
                        'average_score': round((total_correct_subj / total_possible_subj) * 100, 1) if total_possible_subj > 0 else 0
                    }
                context['grade_distribution_report'] = processed_grade_dist_report
        # --- ✨ КОНЕЦ ИСПРАВЛЕНИЙ В РАСЧЕТАХ ✨ ---

    # Возвращаем контекст в любом случае (даже если has_results == False)
    return render(request, 'statistics/statistics.html', context)