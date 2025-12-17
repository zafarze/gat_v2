# D:\New_GAT\core\views\statistics.py

import json
from collections import defaultdict
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Avg, Sum, Count, Q

# Импорты из вашего проекта
from ..models import StudentResult, Subject, SchoolClass, GatTest, QuestionCount
from ..forms import StatisticsFilterForm
from .. import utils
from .permissions import get_accessible_schools
from accounts.models import UserProfile

@login_required
def statistics_view(request):
    """
    Отображает страницу 'Статистика'.
    ИСПРАВЛЕНО: График 'Общая успеваемость' теперь совпадает с таблицей 'По предметам'.
    Мы считаем количество КАЖДОЙ оценки, а не средний балл ученика.
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
        'selected_subject_ids': selected_subject_ids_str,
        'selected_subject_ids_json': json.dumps(selected_subject_ids_str),
    }

    # Инициализация переменных для графика и KPI
    grade_distribution = defaultdict(int)  # Распределение оценок (1-10)
    student_performance = defaultdict(lambda: {'total_score': 0, 'total_possible': 0, 'subjects': defaultdict(list)})
    
    # Структура для таблицы по предметам
    # subject_name -> class_name -> {'grades_list': [], 'correct_total': 0, 'possible_total': 0}
    grade_distribution_report = defaultdict(lambda: defaultdict(lambda: {'grades_list': [], 'correct_total': 0, 'possible_total': 0}))

    if form.is_valid():
        # ... (Код фильтрации остается прежним) ...
        schools = form.cleaned_data.get('schools')
        school_classes = form.cleaned_data.get('school_classes')
        subjects = form.cleaned_data.get('subjects')
        quarters = form.cleaned_data.get('quarters')

        results_qs = StudentResult.objects.select_related(
            'student', 'gat_test', 'student__school_class'
        ).filter(gat_test__quarter__in=quarters)

        if schools:
            results_qs = results_qs.filter(gat_test__school_class__school__in=schools)
        if school_classes:
            results_qs = results_qs.filter(gat_test__school_class__in=school_classes)
        
        # Получаем QuestionCount для правильного расчета процентов
        q_counts_qs = QuestionCount.objects.filter(
            subject__in=subjects if subjects else Subject.objects.all()
        )
        # Карта: Parallel_ID -> Subject_ID -> Max_Questions
        q_counts_map = defaultdict(dict)
        for qc in q_counts_qs:
            pid = qc.school_class.id # ID параллели (например, 10 класс)
            q_counts_map[pid][qc.subject.id] = qc.number_of_questions

        has_results = results_qs.exists()
        context['has_results'] = has_results

        if has_results:
            # === ГЛАВНЫЙ ЦИКЛ ОБРАБОТКИ ===
            for res in results_qs:
                student = res.student
                # Определяем параллель (Parent Class)
                cls = student.school_class
                parallel_id = cls.parent_id if cls.parent_id else cls.id
                
                # Перебираем результаты по предметам внутри JSON
                if isinstance(res.scores_by_subject, dict):
                    for subject_id_str, answers in res.scores_by_subject.items():
                        # Фильтр по предмету (если выбран в форме)
                        if subjects and int(subject_id_str) not in [s.id for s in subjects]:
                            continue
                            
                        # Получаем макс. балл из карты QuestionCount
                        subject_id = int(subject_id_str)
                        max_score = q_counts_map.get(parallel_id, {}).get(subject_id, 0)
                        
                        if max_score == 0: continue # Пропускаем, если нет данных о вопросах

                        # Считаем балл ученика
                        student_score = 0
                        if isinstance(answers, dict):
                             student_score = sum(1 for v in answers.values() if v is True)
                        
                        # === 🔥 ИСПРАВЛЕНИЕ ЗДЕСЬ 🔥 ===
                        # Раньше мы считали график ПОТОМ, по среднему баллу ученика.
                        # Теперь мы считаем график ЗДЕСЬ, по каждой полученной оценке.
                        
                        percent = (student_score / max_score) * 100
                        grade = utils.calculate_grade_from_percentage(percent)
                        
                        # 1. Добавляем в общий график (Итог)
                        grade_distribution[grade] += 1
                        
                        # 2. Добавляем в данные для KPI (средний процент по школе)
                        student_performance[student.id]['total_score'] += student_score
                        student_performance[student.id]['total_possible'] += max_score

                        # 3. Добавляем в таблицу "Отчет по предметам"
                        # Нам нужно имя предмета. Это чуть медленно, но работает.
                        # Оптимизация: можно создать subject_map за пределами цикла
                        try:
                            subj_obj = subjects.get(id=subject_id) if subjects else Subject.objects.get(id=subject_id)
                            subj_name = subj_obj.name
                        except Subject.DoesNotExist:
                            subj_name = f"Subject {subject_id}"

                        class_name = cls.name
                        
                        # Записываем в отчет
                        grade_distribution_report[subj_name][class_name]['grades_list'].append(grade)
                        grade_distribution_report[subj_name][class_name]['correct_total'] += student_score
                        grade_distribution_report[subj_name][class_name]['possible_total'] += max_score

            # === ПОДГОТОВКА ДАННЫХ ДЛЯ ШАБЛОНА ===
            
            # 1. KPI: Общий процент успеваемости
            total_correct_all = sum(d['total_score'] for d in student_performance.values())
            total_possible_all = sum(d['total_possible'] for d in student_performance.values())
            
            avg_percentage = 0
            if total_possible_all > 0:
                avg_percentage = round((total_correct_all / total_possible_all) * 100, 1)
            
            context['average_score'] = avg_percentage
            context['total_students'] = len(student_performance)

            # 2. Данные для Графика (Итог)
            # Теперь grade_distribution содержит сумму всех 10-к, 9-к и т.д. по всем предметам
            context['grade_labels'] = list(grade_distribution.keys())
            context['grade_data'] = list(grade_distribution.values())
            
            # Подготовка данных для Chart.js (сортируем 1..10)
            sorted_grades = sorted(grade_distribution.keys())
            context['chart_labels'] = sorted_grades
            context['chart_data'] = [grade_distribution[g] for g in sorted_grades]

            # 3. Таблица "Отчет по предметам"
            context['grade_range'] = range(10, 0, -1) # 10, 9, ... 1
            
            # Превращаем defaultdict в обычный dict и считаем итоги по предметам
            processed_grade_dist_report = {}
            
            for subject_name, class_data in grade_distribution_report.items():
                processed_grade_dist_report[subject_name] = {}
                
                total_grades_list = []
                total_correct_subj = 0
                total_possible_subj = 0

                for class_name, data in class_data.items():
                    grades_list = data['grades_list']
                    correct_class, possible_class = data['correct_total'], data['possible_total']
                    
                    processed_grade_dist_report[subject_name][class_name] = {
                        'grades': {g: grades_list.count(g) for g in context['grade_range']},
                        'average_score': round((correct_class / possible_class) * 100, 1) if possible_class > 0 else 0
                    }
                    
                    total_grades_list.extend(grades_list)
                    total_correct_subj += correct_class
                    total_possible_subj += possible_class

                # ИТОГ ПО ПРЕДМЕТУ (Последняя колонка)
                processed_grade_dist_report[subject_name]['Итог'] = {
                    'grades': {g: total_grades_list.count(g) for g in context['grade_range']},
                    'average_score': round((total_correct_subj / total_possible_subj) * 100, 1) if total_possible_subj > 0 else 0
                }
            
            context['grade_distribution_report'] = processed_grade_dist_report

            # 4. График по предметам (Top subjects)
            subj_perf_labels = []
            subj_perf_data = []
            
            for s_name, data in processed_grade_dist_report.items():
                if 'Итог' in data:
                    subj_perf_labels.append(s_name)
                    subj_perf_data.append(data['Итог']['average_score'])
            
            context['subject_perf_labels'] = json.dumps(subj_perf_labels, ensure_ascii=False)
            context['subject_perf_data'] = json.dumps(subj_perf_data)

    return render(request, 'statistics/statistics.html', context)