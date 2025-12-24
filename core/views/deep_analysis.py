# D:\New_GAT\core\views\deep_analysis.py

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile

from ..models import SchoolClass, Subject, StudentResult, GatTest
from ..forms import DeepAnalysisForm
from .permissions import get_accessible_schools

@login_required
def deep_analysis_view(request):
    """
    Отображает страницу углубленного анализа с поддержкой сравнения GAT-тестов.
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    form = DeepAnalysisForm(request.GET or None, user=user)

    # Получаем сырые данные для сохранения состояния фильтров в JS и шаблоне
    selected_quarter_ids_str = request.GET.getlist('quarters')
    selected_school_ids_str = request.GET.getlist('schools')
    selected_class_ids_str = request.GET.getlist('school_classes')
    selected_subject_ids_str = request.GET.getlist('subjects')
    
    # Эти параметры нужны для работы JavaScript фильтров
    context = {
        'title': 'Углубленный анализ',
        'form': form,
        'has_results': False,
        'selected_quarter_ids': selected_quarter_ids_str,
        'selected_school_ids': selected_school_ids_str,
        'selected_class_ids': selected_class_ids_str,
        'selected_class_ids_json': json.dumps(selected_class_ids_str),
        'selected_subject_ids_json': json.dumps(selected_subject_ids_str),
        'summary_chart_data': None,
        'comparison_chart_data': None,
        'heatmap_data': {},
        'heatmap_summary': {},
        'trend_chart_data': None,
        'problematic_questions': {},
        'at_risk_students': [],
    }

    # --- Группировка классов для красивого Select (UI) ---
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
        except ValueError:
            pass

    final_grouped_classes = {}
    sorted_group_items = sorted(grouped_classes.items(), key=lambda item: (not item[0].endswith("(Параллель)"), item[0]))
    for group_name, classes_in_group in sorted_group_items:
        classes_in_group.sort(key=lambda x: x.name)
        final_grouped_classes[group_name] = classes_in_group
    context['grouped_classes'] = final_grouped_classes

    # --- ОСНОВНАЯ ЛОГИКА ---
    if form.is_valid():
        selected_quarters = form.cleaned_data['quarters']
        selected_schools = form.cleaned_data['schools']
        selected_classes_qs = form.cleaned_data['school_classes']
        selected_subjects_qs = form.cleaned_data['subjects']
        
        # Получаем номера тестов (конвертируем в int для надежности)
        raw_test_numbers = form.cleaned_data['test_numbers']
        selected_test_numbers = [int(n) for n in raw_test_numbers]
        
        selected_days = form.cleaned_data['days']

        # Подготовка списка ID классов (включая подклассы)
        selected_class_ids_list = list(selected_classes_qs.values_list('id', flat=True))
        parent_class_ids = selected_classes_qs.filter(parent__isnull=True).values_list('id', flat=True)
        if parent_class_ids:
            child_class_ids = list(SchoolClass.objects.filter(parent_id__in=parent_class_ids).values_list('id', flat=True))
            selected_class_ids_list.extend(child_class_ids)
        final_class_ids = set(selected_class_ids_list)

        # Базовый QuerySet
        accessible_schools = get_accessible_schools(user)
        base_qs = StudentResult.objects.filter(student__school_class__school__in=accessible_schools)

        results_qs = base_qs.filter(
            gat_test__quarter__in=selected_quarters,
            gat_test__test_number__in=selected_test_numbers,
        ).select_related('student__school_class__school', 'gat_test__quarter')

        if selected_schools:
            results_qs = results_qs.filter(student__school_class__school__in=selected_schools)
        if final_class_ids:
            results_qs = results_qs.filter(student__school_class_id__in=final_class_ids)
        if selected_days:
            results_qs = results_qs.filter(gat_test__day__in=selected_days)

        # Фильтрация по предметам (для экспертов)
        accessible_subjects_qs = Subject.objects.none()
        is_expert = profile and profile.role == UserProfile.Role.EXPERT
        
        if is_expert:
            expert_subjects = profile.subjects.all()
            expert_subject_ids_int = set(expert_subjects.values_list('id', flat=True))
            if selected_subjects_qs.exists():
                accessible_subjects_qs = selected_subjects_qs.filter(id__in=expert_subject_ids_int)
            elif expert_subjects.exists():
                accessible_subjects_qs = expert_subjects
        else:
            accessible_subjects_qs = selected_subjects_qs

        # Фильтр JSON-поля scores_by_subject
        if accessible_subjects_qs.exists():
            subject_id_keys_to_filter = [str(s.id) for s in accessible_subjects_qs]
            results_qs = results_qs.filter(scores_by_subject__has_any_keys=subject_id_keys_to_filter)
        elif is_expert:
             results_qs = results_qs.none()

        # Если предметы не выбраны, берем все, что есть в результатах
        if not accessible_subjects_qs.exists() and not is_expert and results_qs.exists():
             all_subject_ids_in_results = set()
             for r in results_qs:
                 if isinstance(r.scores_by_subject, dict):
                     all_subject_ids_in_results.update(int(sid) for sid in r.scores_by_subject.keys())
             accessible_subjects_qs = Subject.objects.filter(id__in=all_subject_ids_in_results)

        # --- ✨ НОВАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ COMPARE_BY ✨ ---
        if results_qs.exists() and accessible_subjects_qs.exists():
            
            # Считаем количество выбранных сущностей
            tests_count = len(selected_test_numbers)
            quarters_count = selected_quarters.count()
            classes_selected_count = selected_classes_qs.filter(parent__isnull=False).count()
            schools_selected_count = selected_schools.count() if selected_schools else accessible_schools.count()

            compare_by = 'school' # По умолчанию
            
            # 1. Если выбрано несколько Тестов или Четвертей -> Сравниваем ТЕСТЫ (Динамика)
            # Это решит вашу проблему: теперь при выборе GAT-1 и GAT-2 включится этот режим
            if tests_count > 1 or quarters_count > 1:
                compare_by = 'test'
            # 2. Если выбрана 1 школа и несколько классов -> Сравниваем КЛАССЫ
            elif schools_selected_count == 1 and classes_selected_count > 1:
                compare_by = 'class'
            # 3. Иначе (много школ или ничего не выбрано) -> Сравниваем ШКОЛЫ
            else:
                compare_by = 'school'

            unique_subject_names = sorted(list(set(accessible_subjects_qs.values_list('name', flat=True))))
            subject_id_to_name_map = {s.id: s.name for s in accessible_subjects_qs}
            allowed_subject_ids_int = set(subject_id_to_name_map.keys())

            # Запускаем обработку с новым compare_by
            analysis_data, student_performance, new_unique_subjects = _process_results_for_deep_analysis(
                results_qs, unique_subject_names, subject_id_to_name_map, allowed_subject_ids_int, compare_by
            )

            summary_chart_data, comparison_chart_data = _prepare_summary_charts(
                analysis_data, new_unique_subjects
            )

            heatmap_data, heatmap_summary = _prepare_heatmap_data_and_summary(analysis_data)
            trend_chart_data = _prepare_trend_chart_data(results_qs, allowed_subject_ids_int, subject_id_to_name_map)
            problematic_questions = _find_problematic_questions(analysis_data)
            at_risk_students = _find_at_risk_students(student_performance)

            context.update({
                'has_results': True,
                'summary_chart_data': json.dumps(summary_chart_data, ensure_ascii=False),
                'comparison_chart_data': json.dumps(comparison_chart_data, ensure_ascii=False),
                'heatmap_data': heatmap_data,
                'heatmap_summary': heatmap_summary,
                'trend_chart_data': json.dumps(trend_chart_data, ensure_ascii=False) if trend_chart_data else None,
                'problematic_questions': problematic_questions,
                'at_risk_students': at_risk_students,
            })

    return render(request, 'deep_analysis.html', context)


# ==========================================================
# --- Вспомогательные функции ---
# ==========================================================

def _process_results_for_deep_analysis(results_qs, unique_subject_names, subject_id_to_name_map, allowed_subject_ids_int, compare_by='school'):
    """ 
    Обрабатывает результаты. 
    Поддерживает compare_by = 'school', 'class', 'test'.
    """
    temp_analysis_data = {}
    student_performance = defaultdict(lambda: {
        'subject_scores': defaultdict(list),
        'name': '', 'class_name': '', 'school_name': ''
    })

    dynamic_subjects = set() 

    # Оптимизация: предзагрузка
    results_qs = results_qs.select_related('student__school_class', 'student__school_class__school', 'student__school_class__parent', 'gat_test', 'gat_test__quarter')

    for result in results_qs:
        student = result.student
        school_class = student.school_class
        school = school_class.school
        gat_test = result.gat_test
        
        # Определяем параллель для названия предмета
        parallel_name = school_class.parent.name if school_class.parent else school_class.name
        
        # --- ✨ ЛОГИКА ОПРЕДЕЛЕНИЯ СУЩНОСТИ ДЛЯ СРАВНЕНИЯ ✨ ---
        if compare_by == 'test':
            # Сравниваем GAT-1 vs GAT-2 (или по четвертям)
            # Группируем по уникальному сочетанию Теста и Четверти
            entity_id = f"{gat_test.quarter.id}_{gat_test.test_number}" # ID для сортировки (Квартал потом Тест)
            entity_name = f"GAT-{gat_test.test_number} ({gat_test.quarter.name})"
        elif compare_by == 'class':
            # Сравниваем Классы
            entity_id = str(school_class.id)
            entity_name = school_class.name
        else:
            # Сравниваем Школы
            entity_id = str(school.id)
            entity_name = school.name

        if entity_id not in temp_analysis_data:
            temp_analysis_data[entity_id] = {
                'name': entity_name,
                'subjects': {}
            }

        student_id = student.id
        student_performance[student_id].update({
            'name': str(student),
            'class_name': school_class.name,
            'school_name': school.name
        })

        if not isinstance(result.scores_by_subject, dict):
            continue

        for subject_id_str, answers in result.scores_by_subject.items():
            try:
                subject_id = int(subject_id_str)
                if subject_id in allowed_subject_ids_int:
                    base_subject_name = subject_id_to_name_map.get(subject_id)
                    if not base_subject_name or not isinstance(answers, dict): continue
                    
                    # Имя предмета включает параллель, чтобы не смешивать программы 5 и 10 классов
                    full_subject_name = f"{base_subject_name} ({parallel_name})"
                    dynamic_subjects.add(full_subject_name)

                    if full_subject_name not in temp_analysis_data[entity_id]['subjects']:
                         temp_analysis_data[entity_id]['subjects'][full_subject_name] = {
                             'question_details': defaultdict(lambda: {'correct': 0, 'total': 0})
                         }

                    data_ref = temp_analysis_data[entity_id]['subjects'][full_subject_name]
                    correct_count = 0
                    
                    for q_num, was_correct in answers.items():
                        if was_correct:
                            data_ref['question_details'][q_num]['correct'] += 1
                            correct_count += 1
                        data_ref['question_details'][q_num]['total'] += 1
                    
                    total_questions = len(answers)
                    if total_questions > 0:
                        percentage = (correct_count / total_questions) * 100
                        student_performance[student_id]['subject_scores'][full_subject_name].append(percentage)

            except (ValueError, TypeError):
                continue

    # Подсчет процентов
    for entity_data in temp_analysis_data.values():
        for subject_data in entity_data['subjects'].values():
            total_correct, total_q = 0, 0
            for q_data in subject_data['question_details'].values():
                if q_data['total'] > 0:
                    q_data['percentage'] = round((q_data['correct'] / q_data['total']) * 100, 1)
                    total_correct += q_data['correct']
                    total_q += q_data['total']
            subject_data['overall_percentage'] = round((total_correct / total_q) * 100, 1) if total_q > 0 else 0

    sorted_dynamic_subjects = sorted(list(dynamic_subjects))
    return temp_analysis_data, student_performance, sorted_dynamic_subjects


def _prepare_summary_charts(analysis_data, unique_subject_names):
    """ Готовит данные для графика общей успеваемости и графика сравнения по предметам. """
    bar_datasets_summary = []
    bar_datasets_comparison = []

    # 1. График общей успеваемости
    overall_subject_averages = []
    for name in unique_subject_names:
        all_correct, all_total = 0, 0
        for entity_data in analysis_data.values():
            q_details = entity_data['subjects'].get(name, {}).get('question_details', {})
            for q_data in q_details.values():
                all_correct += q_data.get('correct', 0)
                all_total += q_data.get('total', 0)
        avg = round((all_correct / all_total) * 100, 1) if all_total > 0 else 0
        overall_subject_averages.append(avg)

    bar_datasets_summary.append({
        'label': 'Среднее по всем', 'data': overall_subject_averages,
    })

    # 2. График сравнения
    # ✨ ВАЖНО: Сортируем сущности по ключу (ID), чтобы GAT-1 шел перед GAT-2
    # Ключи у нас вида "QuarterID_TestNumber" (например "1_1", "1_2"), что дает верную сортировку
    sorted_entity_ids = sorted(analysis_data.keys())

    for ent_id in sorted_entity_ids:
        entity_data = analysis_data[ent_id]
        entity_name = entity_data['name']
        data_points = [entity_data['subjects'].get(name, {}).get('overall_percentage', 0) for name in unique_subject_names]
        bar_datasets_comparison.append({'label': entity_name, 'data': data_points})
    
    # Линия среднего
    bar_datasets_comparison.append({
        'label': 'Среднее', 'data': overall_subject_averages,
        'type': 'line', 'borderDash': [5, 5], 'borderWidth': 2, 'pointRadius': 0,
        'datalabels': {'display': False}
    })

    summary_chart = {'labels': unique_subject_names, 'datasets': bar_datasets_summary}
    comparison_chart = {'labels': unique_subject_names, 'datasets': bar_datasets_comparison}
    
    return summary_chart, comparison_chart


def _prepare_heatmap_data_and_summary(analysis_data):
    """ Готовит данные для тепловой карты. """
    heatmap_data = {}
    heatmap_summary = {}

    for entity_id, entity_data in analysis_data.items():
        entity_name = entity_data['name']
        for subject_name, subject_data in entity_data['subjects'].items():
            if not subject_data.get('question_details'): continue

            if subject_name not in heatmap_data:
                heatmap_data[subject_name] = {'questions': set(), 'schools': {}}

            heatmap_data[subject_name]['schools'][entity_name] = {}
            for q_num, q_stats in subject_data['question_details'].items():
                heatmap_data[subject_name]['questions'].add(q_num)
                heatmap_data[subject_name]['schools'][entity_name][q_num] = {
                    'percentage': q_stats.get('percentage', 0),
                    'correct': q_stats.get('correct', 0),
                    'total': q_stats.get('total', 0),
                }

    for subject_name, data in list(heatmap_data.items()):
        if not data['questions']:
             del heatmap_data[subject_name]
             continue

        data['questions'] = sorted(list(data['questions']), key=int)

        question_avg_perf = []
        for q_num in data['questions']:
            total_correct, total_answers = 0, 0
            for school_q_data in data['schools'].values():
                q_data = school_q_data.get(q_num)
                if q_data:
                    total_correct += q_data.get('correct', 0)
                    total_answers += q_data.get('total', 0)
            avg_p = round((total_correct / total_answers) * 100, 1) if total_answers > 0 else 0
            question_avg_perf.append({'q_num': q_num, 'percentage': avg_p})

        sorted_by_perf = sorted(question_avg_perf, key=lambda x: x['percentage'], reverse=True)
        
        entity_perf_list = []
        total_correct_all, total_q_all = 0, 0
        for entity_name, entity_q_data in data['schools'].items():
            e_total_correct, e_total_q = 0, 0
            for q in entity_q_data.values():
                e_total_correct += q.get('correct', 0)
                e_total_q += q.get('total', 0)
            e_avg = round((e_total_correct / e_total_q) * 100, 1) if e_total_q > 0 else 0
            entity_perf_list.append({'school': entity_name, 'avg': e_avg})
            total_correct_all += e_total_correct
            total_q_all += e_total_q

        sorted_entities = sorted(entity_perf_list, key=lambda x: x['avg'], reverse=True)
        overall_avg = round((total_correct_all / total_q_all) * 100, 1) if total_q_all > 0 else 0
        difference = round(sorted_entities[0]['avg'] - sorted_entities[-1]['avg'], 1) if len(sorted_entities) > 1 else 0

        heatmap_summary[subject_name] = {
            'easiest': sorted_by_perf[:3] if sorted_by_perf else [],
            'hardest': sorted_by_perf[-3:][::-1] if sorted_by_perf else [],
            'ranking': sorted_entities,
            'overall_avg': overall_avg,
            'difference': difference,
        }

    return heatmap_data, heatmap_summary


def _prepare_trend_chart_data(results_qs, allowed_subject_ids_int, subject_id_to_name_map):
    """ Готовит данные для графика динамики по четвертям (без изменений). """
    quarters_with_results = results_qs.values_list('gat_test__quarter', flat=True).distinct()
    if quarters_with_results.count() < 2: return None

    trend_data = defaultdict(lambda: defaultdict(lambda: {'correct': 0, 'total': 0}))
    all_quarters = set()

    for r in results_qs.select_related('gat_test__quarter'):
        quarter = r.gat_test.quarter
        all_quarters.add((quarter.start_date, quarter.name))

        if not isinstance(r.scores_by_subject, dict): continue

        for sid_str, answers in r.scores_by_subject.items():
            try:
                sid = int(sid_str)
                if sid in allowed_subject_ids_int:
                    s_name = subject_id_to_name_map.get(sid)
                    if s_name and isinstance(answers, dict):
                        trend_data[s_name][quarter.name]['correct'] += sum(1 for v in answers.values() if v)
                        trend_data[s_name][quarter.name]['total'] += len(answers)
            except (ValueError, TypeError):
                 continue

    sorted_quarters = [name for date, name in sorted(list(all_quarters))]

    trend_datasets = []
    for subject, quarters_data in trend_data.items():
        data_points = []
        for q_name in sorted_quarters:
            data = quarters_data.get(q_name)
            if data and data['total'] > 0:
                data_points.append(round((data['correct'] / data['total']) * 100, 1))
            else:
                data_points.append(None)
        trend_datasets.append({'label': subject, 'data': data_points, 'tension': 0.4, 'fill': True})

    return {'labels': sorted_quarters, 'datasets': trend_datasets}


def _find_problematic_questions(analysis_data, top_n=3):
    """ Находит топ N самых сложных вопросов. """
    problems = defaultdict(list)

    for entity_data in analysis_data.values():
        entity_name = entity_data['name']
        for s_name, s_data in entity_data['subjects'].items():
            for q_num, q_stats in s_data.get('question_details', {}).items():
                if q_stats.get('total', 0) > 0:
                    problems[s_name].append({
                        'q': q_num,
                        'p': q_stats.get('percentage', 0),
                        'school': entity_name
                    })

    top_problems = {}
    for s_name, q_list in problems.items():
        sorted_q = sorted(q_list, key=lambda x: x['p'])
        top_problems[s_name] = sorted_q[:top_n]

    return top_problems


def _find_at_risk_students(student_performance, threshold=40):
    """ Находит учеников в группе риска. """
    at_risk = []
    
    for student_id, data in student_performance.items():
        for subject_name, scores in data.get('subject_scores', {}).items():
            if not scores: continue
            
            avg_score = sum(scores) / len(scores)
            if avg_score < threshold:
                at_risk.append({
                    'name': data['name'],
                    'class': data['class_name'],
                    'school': data['school_name'],
                    'subject': subject_name,
                    'score': round(avg_score, 1)
                })

    return sorted(at_risk, key=lambda x: x['score'])