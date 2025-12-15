# D:\New_GAT\core\views\utils_reports.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from collections import defaultdict
from types import SimpleNamespace 
from accounts.models import UserProfile
from core.forms import MonitoringFilterForm
from core.models import StudentResult, SchoolClass, Subject, QuestionCount
from core.views.permissions import get_accessible_schools
from core import utils as grade_utils
from django.db.models import Q


def get_report_context(get_params, request_user, mode='monitoring'):
    user = request_user
    profile = getattr(user, 'profile', None)
    form = MonitoringFilterForm(get_params or None, user=user)

    table_headers = []
    table_rows = []
    title_details = {}
    accessible_subjects_for_user = None

    accessible_schools = get_accessible_schools(user)
    base_results_qs = StudentResult.objects.filter(
        student__school_class__school__in=accessible_schools
    ).select_related(
        'student__school_class__school',
        'student__school_class__parent',
        'gat_test__quarter__year',
        'gat_test__school_class'
    )

    is_expert = profile and profile.role == UserProfile.Role.EXPERT
    is_teacher_or_homeroom = profile and profile.role in [UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]

    if is_expert or is_teacher_or_homeroom:
        accessible_subjects_for_user = profile.subjects.all()
        if not accessible_subjects_for_user.exists():
             base_results_qs = base_results_qs.none()

    subjects_filter_from_form = Subject.objects.none()
    valid_form_filters = Q()
    
    days_selected = []
    should_group_days = False

    if form.is_valid():
        subjects_filter_from_form = form.cleaned_data.get('subjects')
        days_selected = form.cleaned_data.get('days', []) 

        # --- Группируем для ВСЕХ режимов, если выбрано 0 или 2 дня ---
        if len(days_selected) != 1:
            should_group_days = True
        # ---

        if quarters := form.cleaned_data.get('quarters'):
            valid_form_filters &= Q(gat_test__quarter__in=quarters)
            title_details['period'] = ", ".join([str(q) for q in quarters])
        if schools := form.cleaned_data.get('schools'):
            valid_form_filters &= Q(student__school_class__school__in=schools)
            title_details['schools'] = ", ".join([s.name for s in schools])
        if school_classes := form.cleaned_data.get('school_classes'):
            selected_class_ids = list(school_classes.values_list('id', flat=True))
            subclass_ids = list(SchoolClass.objects.filter(parent__in=selected_class_ids).values_list('id', flat=True))
            all_relevant_class_ids = set(selected_class_ids + subclass_ids)
            valid_form_filters &= Q(student__school_class_id__in=all_relevant_class_ids)
            title_details['classes'] = ", ".join([c.name for c in school_classes])
        if test_numbers := form.cleaned_data.get('test_numbers'):
            valid_form_filters &= Q(gat_test__test_number__in=test_numbers)
            title_details['test_type'] = ", ".join([f"GAT-{num}" for num in test_numbers])
        
        if days_selected and not should_group_days:
            valid_form_filters &= Q(gat_test__day__in=days_selected)

    results_qs = base_results_qs.filter(valid_form_filters)

    # --- Определение `has_both_days` ---
    student_test_days = defaultdict(set)
    day_data = results_qs.values(
        'student_id',
        'gat_test__test_number',
        'gat_test__day'
    ).distinct()
    for item in day_data:
        student_test_days[(item['student_id'], item['gat_test__test_number'])].add(item['gat_test__day'])
    students_with_both_days = {
        key for key, days_set in student_test_days.items() if days_set == {1, 2}
    }
    # ---

    # --- Фильтрация по ПРЕДМЕТАМ ---
    final_subject_ids_to_filter = set()
    apply_subject_filter_qs = False
    if is_expert or is_teacher_or_homeroom:
        user_subject_ids = set(accessible_subjects_for_user.values_list('id', flat=True))
        if subjects_filter_from_form.exists():
            form_subject_ids = set(subjects_filter_from_form.values_list('id', flat=True))
            final_subject_ids_to_filter = user_subject_ids.intersection(form_subject_ids)
        else:
            final_subject_ids_to_filter = user_subject_ids
        apply_subject_filter_qs = True
    elif subjects_filter_from_form.exists():
        final_subject_ids_to_filter = set(subjects_filter_from_form.values_list('id', flat=True))
        apply_subject_filter_qs = True
    if apply_subject_filter_qs:
        if final_subject_ids_to_filter:
            subject_keys_to_filter = [str(sid) for sid in final_subject_ids_to_filter]
            results_qs = results_qs.filter(scores_by_subject__has_any_keys=subject_keys_to_filter)
        else:
            results_qs = results_qs.none()
    # ---

    # --- Определение `header_subjects` ---
    subject_map_all = {s.id: s for s in Subject.objects.all()}
    header_subjects = []
    if results_qs.exists():
        ids_for_header = set()
        if is_expert or is_teacher_or_homeroom:
            ids_for_header = final_subject_ids_to_filter
        elif subjects_filter_from_form.exists():
            ids_for_header = final_subject_ids_to_filter
        else:
            scores_list = results_qs.values_list('scores_by_subject', flat=True)
            for scores_dict in scores_list:
                if isinstance(scores_dict, dict):
                    ids_for_header.update(int(sid) for sid in scores_dict.keys())
        header_subjects = sorted(
            [subject_map_all[sid] for sid in ids_for_header if sid in subject_map_all],
            key=lambda s: s.name
        )
    # ---

    # --- Расчет `q_counts` ---
    q_counts = {}
    ref_classes_qs = SchoolClass.objects.none()
    if results_qs.exists() and header_subjects:
        class_ids_in_final_results = results_qs.values_list('student__school_class_id', flat=True).distinct()
        ref_classes_qs = SchoolClass.objects.filter(
            id__in=class_ids_in_final_results
        ).select_related('parent')
        all_class_ids_for_qc = set(ref_classes_qs.values_list('id', flat=True))
        all_class_ids_for_qc.update(
            ref_classes_qs.exclude(parent__isnull=True).values_list('parent_id', flat=True)
        )
        all_class_ids_for_qc.discard(None)
        question_counts_qs = QuestionCount.objects.filter(
            school_class_id__in=all_class_ids_for_qc,
            subject__in=header_subjects
        )
        q_counts_map = defaultdict(dict)
        for qc in question_counts_qs:
            q_counts_map[qc.subject_id][qc.school_class_id] = qc.number_of_questions
        for subj in header_subjects:
            for cls in ref_classes_qs:
                if cls.id in q_counts_map.get(subj.id, {}):
                    q_counts[(subj.id, cls.id)] = q_counts_map[subj.id][cls.id]
                elif cls.parent_id and cls.parent_id in q_counts_map.get(subj.id, {}):
                    q_counts[(subj.id, cls.id)] = q_counts_map[subj.id][cls.parent_id]
    # ---

    # --- Формирование заголовков (`table_headers`) ---
    for subj in header_subjects:
        representative_q_count = 0
        if ref_classes_qs.exists():
             first_class_id = ref_classes_qs.first().id
             representative_q_count = q_counts.get((subj.id, first_class_id), 0)
        table_headers.append({'subject': subj, 'q_count': representative_q_count})


    # --- Формирование строк таблицы (`table_rows`) ---
    if results_qs.exists():
        
        # --- ЛОГИКА A: Группируем дни (0 или 2 дня выбрано) ---
        if should_group_days:
            grouped_rows = defaultdict(lambda: {
                'scores_by_subject': defaultdict(lambda: {'score': 0, 'total': 0}),
                'total_score': 0, 
            })
            student_map = {} 

            for result in results_qs.distinct().select_related('student__school_class', 'gat_test'):
                if not (result.student and result.student.school_class and isinstance(result.scores_by_subject, dict)):
                    continue
                
                key = (result.student_id, result.gat_test.test_number)
                
                if result.student_id not in student_map:
                    student_map[key] = result.student

                for header in table_headers:
                    header_subject = header['subject']
                    subject_id, subject_id_str = header_subject.id, str(header_subject.id)
                    answers = result.scores_by_subject.get(subject_id_str)
                    q_count = q_counts.get((subject_id, result.student.school_class_id), 0)

                    current_score_data = grouped_rows[key]['scores_by_subject'][subject_id]
                    
                    if answers is not None and isinstance(answers, dict):
                        score = sum(1 for v in answers.values() if v is True)
                        grouped_rows[key]['total_score'] += score
                        
                        current_score_data['score'] += score
                        current_score_data['total'] = q_count 
                    
                    elif subject_id not in grouped_rows[key]['scores_by_subject']:
                        current_score_data['total'] = q_count

            # Конвертируем сгруппированные данные в `table_rows`
            for (student_id, test_number), data in grouped_rows.items():
                student_obj = student_map.get((student_id, test_number))
                if not student_obj: continue
                
                fake_test_name = f"GAT-{test_number} (Total)"
                fake_test = SimpleNamespace(
                    name=fake_test_name,
                    test_number=test_number,
                    day=0
                )
                fake_result_obj = SimpleNamespace(gat_test=fake_test)

                final_grades_by_subject = {}
                total_grade_points = 0
                subjects_in_row = 0

                if mode == 'grading':
                    for subject_id, score_data in data['scores_by_subject'].items():
                        score = score_data['score']
                        total = score_data['total'] 
                        
                        if total > 0:
                            percentage = (score / total) * 100
                            grade = grade_utils.calculate_grade_from_percentage(percentage)
                            final_grades_by_subject[subject_id] = grade
                            total_grade_points += grade
                            subjects_in_row += 1
                        else:
                            final_grades_by_subject[subject_id] = "—"
                
                if mode == 'grading':
                    final_total_score = total_grade_points if subjects_in_row > 0 else 0
                else: 
                    final_total_score = data['total_score']

                table_rows.append({
                    'student': student_obj,
                    'result_obj': fake_result_obj, 
                    'scores_by_subject': data['scores_by_subject'],
                    'grades_by_subject': final_grades_by_subject,
                    'total_score': final_total_score,
                    'has_both_days': (student_id, test_number) in students_with_both_days
                })

        # --- ЛОГИКА B: Не группируем (1 день выбран) ---
        else:
            for result in results_qs.distinct().select_related('student__school_class', 'gat_test'):
                if not (result.student and result.student.school_class and isinstance(result.scores_by_subject, dict)):
                    continue

                current_key = (result.student_id, result.gat_test.test_number)
                has_both_days = current_key in students_with_both_days

                row_data = {
                    'student': result.student,
                    'result_obj': result, 
                    'scores_by_subject': {},
                    'grades_by_subject': {},
                    'total_score': 0,
                    'has_both_days': has_both_days
                }
                total_grade_points, subjects_in_row = 0, 0

                for header in table_headers:
                    header_subject = header['subject']
                    subject_id, subject_id_str = header_subject.id, str(header_subject.id)
                    answers = result.scores_by_subject.get(subject_id_str)
                    q_count = q_counts.get((subject_id, result.student.school_class_id), 0)

                    if answers is not None and isinstance(answers, dict):
                        score = sum(1 for v in answers.values() if v is True)
                        row_data['total_score'] += score
                        subjects_in_row += 1

                        if mode == 'grading':
                            percentage = (score / q_count) * 100 if q_count > 0 else 0
                            grade = grade_utils.calculate_grade_from_percentage(percentage)
                            row_data['grades_by_subject'][subject_id] = grade
                            total_grade_points += grade
                        else: # mode == 'monitoring'
                            row_data['scores_by_subject'][subject_id] = {'score': score, 'total': q_count}
                    else:
                        if mode == 'grading':
                             row_data['grades_by_subject'][subject_id] = "—"
                        else:
                             row_data['scores_by_subject'][subject_id] = {'score': '—', 'total': q_count}

                if mode == 'grading':
                     row_data['total_score'] = total_grade_points if subjects_in_row > 0 else 0

                table_rows.append(row_data)

    # --- ✨✨✨ ИСПРАВЛЕНИЕ 3: Сортировка по результату (сначала лучшие) ✨✨✨ ---
    sort_key_lambda = (
        lambda x: (
            -x.get('total_score', 0),    # 1. По общему баллу (по убыванию)
            x['student'].last_name_ru,   # 2. По фамилии (по возрастанию)
            x['student'].first_name_ru   # 3. По имени (по возрастанию)
        )
    )
    table_rows.sort(key=sort_key_lambda)
    # --- ✨✨✨ КОНЕЦ ИСПРАВЛЕНИЯ 3 ✨✨✨ ---

    # --- Возвращаем контекст ---
    return {
        'form': form,
        'table_headers': table_headers,
        'table_rows': table_rows,
        'has_results': bool(get_params) and form.is_valid() and table_rows,
        'title_details': title_details,
        'accessible_subjects_for_user': accessible_subjects_for_user,
    }