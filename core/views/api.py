# D:\New_GAT\core\views\api.py (ПОЛНАЯ ОБНОВЛЕННАЯ ВЕРСИЯ)

import json
import pytz
from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from collections import defaultdict
from accounts.forms import UserProfileForm
from ..models import SchoolClass, GatTest, Subject

# --- Импорты моделей ---
from ..models import (
    Notification, School, SchoolClass, Subject, Quarter, GatTest, Student
)
from accounts.models import UserProfile
# --- Импорты форм ---
from ..forms import GatTestForm, QuestionCountForm

# --- Импорты из permissions ---
from .permissions import get_accessible_schools

# =============================================================================
# --- API ДЛЯ ЗАГРУЗКИ ДАННЫХ В ФИЛЬТРЫ И ФОРМЫ (HTMX И JAVASCRIPT) ---
# =============================================================================

@login_required
def load_quarters(request):
    """ API: Загружает <option> с четвертями для выбранного года, учитывая права доступа. """
    year_id = request.GET.get('year_id')
    user = request.user
    context = {'placeholder': 'Сначала выберите год'}

    if year_id:
        quarters_qs = Quarter.objects.filter(year_id=year_id)
        if not user.is_superuser:
            accessible_schools = get_accessible_schools(user)
            quarters_qs = quarters_qs.filter(
                gat_tests__school__in=accessible_schools
            ).distinct()

        context = {
            'items': quarters_qs.order_by('name'),
            'placeholder': 'Выберите четверть...'
        }
    return render(request, 'partials/options.html', context)

@login_required
def load_classes(request):
    """ API: Загружает <option> с классами (в формате "Школа - Класс") для выбранных школ. """
    school_ids = request.GET.getlist('school_ids[]')
    context = {'placeholder': 'Сначала выберите школу'}
    if school_ids:
        classes = SchoolClass.objects.filter(school_id__in=school_ids).select_related('school').order_by('school__name', 'name')
        context = {'items': classes, 'placeholder': 'Выберите класс...'}
    return render(request, 'partials/school_item_options.html', context)

@login_required
def load_subjects(request):
    """ API: Загружает <option> с предметами (в формате "Школа - Предмет") для выбранных школ. """
    school_ids = request.GET.getlist('school_ids[]')
    context = {'placeholder': 'Сначала выберите школу'}
    if school_ids:
        subjects = Subject.objects.filter(school_id__in=school_ids).select_related('school').order_by('school__name', 'name')
        context = {'items': subjects, 'placeholder': 'Выберите предмет...'}
    return render(request, 'partials/school_item_options.html', context)

@login_required
def api_load_classes_as_chips(request):
    """ API (HTMX/JS): Загружает классы в виде "чипов" для выбранных школ. """
    school_ids = request.GET.getlist('schools')
    selected_class_ids = request.GET.getlist('school_classes') 

    if not school_ids:
        return HttpResponse("<p class=\"text-sm text-gray-500\">Сначала выберите школу.</p>")

    classes_qs = SchoolClass.objects.filter(
        school_id__in=school_ids
    ).select_related('parent', 'school').order_by('school__name', 'name')

    grouped_classes = defaultdict(list)
    is_multiple_schools = len(school_ids) > 1

    for cls in classes_qs:
        group_name = ""
        if cls.parent is None:
            group_name = f"{cls.name} классы (Параллель)"
        else:
            group_name = f"{cls.parent.name} классы"

        if is_multiple_schools:
            group_name = f"{cls.school.name} - {group_name}"

        grouped_classes[group_name].append(cls)

    final_grouped_classes = {}
    sorted_group_items = sorted(
        grouped_classes.items(),
        key=lambda item: (not item[0].endswith("(Параллель)"), item[0])
    )
    for group_name, classes_in_group in sorted_group_items:
        classes_in_group.sort(key=lambda x: x.name)
        final_grouped_classes[group_name] = classes_in_group

    context = {
        'grouped_classes': final_grouped_classes,
        'selected_class_ids': selected_class_ids 
    }
    return render(request, 'partials/_class_chips.html', context)

@login_required
def load_subjects_for_filters(request):
    """
    API (JavaScript/HTMX): Загружает предметы в виде JSON для фильтров.
    (Обновлено: для Эксперта и Учителя возвращает только их предметы).
    """
    user = request.user
    profile = getattr(user, 'profile', None)

    # ---> ПРОВЕРКА РОЛИ ЭКСПЕРТА ИЛИ УЧИТЕЛЯ <---
    is_expert = profile and profile.role == UserProfile.Role.EXPERT
    is_teacher_or_homeroom = profile and profile.role in [UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]

    if is_expert or is_teacher_or_homeroom:
         # Если пользователь - Эксперт или Учитель, игнорируем фильтры
         # и возвращаем предметы, назначенные ИМЕННО ЭТОМУ пользователю
         subjects = profile.subjects.all().order_by('name')
         subjects_data = [{
             'id': subject.id,
             'name': subject.name,
             'abbreviation': subject.abbreviation or subject.name[:3].upper()
         } for subject in subjects]
         # Сразу возвращаем JSON с предметами Эксперта/Учителя
         return JsonResponse({'subjects': subjects_data})
    # ---> КОНЕЦ ПРОВЕРКИ <---

    # --- Если пользователь НЕ Эксперт/Учитель (Админ/Директор), выполняем старую логику ---
    school_ids = request.GET.getlist('school_ids[]')
    class_ids = request.GET.getlist('class_ids[]')
    test_numbers = request.GET.getlist('test_numbers[]')
    days = request.GET.getlist('days[]')

    # Проверяем наличие обязательных параметров для Админа/Директора
    if not class_ids or not test_numbers or not school_ids:
        return JsonResponse({'subjects': []})

    try:
        # 1. Находим ID параллелей
        selected_classes = SchoolClass.objects.filter(id__in=class_ids)
        parent_class_ids = set(selected_classes.values_list('parent_id', flat=True))
        parent_class_ids.update(selected_classes.filter(parent__isnull=True).values_list('id', flat=True))
        parent_class_ids.discard(None)

        # 2. Находим тесты
        matching_tests = GatTest.objects.filter(
            school_id__in=school_ids,
            school_class_id__in=parent_class_ids,
            test_number__in=test_numbers
        )
        if days:
            matching_tests = matching_tests.filter(day__in=days)

        # 3. Получаем ID предметов из тестов
        subject_ids = matching_tests.values_list('subjects', flat=True).distinct()
        subject_ids = [sid for sid in subject_ids if sid is not None]

        # 4. Загружаем объекты Subject
        subjects = Subject.objects.filter(id__in=subject_ids).order_by('name')

        # Формируем JSON-ответ
        subjects_data = [{
            'id': subject.id,
            'name': subject.name,
            'abbreviation': subject.abbreviation or subject.name[:3].upper()
        } for subject in subjects]

        return JsonResponse({'subjects': subjects_data})

    except Exception as e:
        print(f"Error in load_subjects_for_filters: {e}")
        return JsonResponse({'error': f'Internal server error: {e}'}, status=500)

# =============================================================================
# --- API ДЛЯ ФОРМ CRUD (HTMX) ---
# =============================================================================

@login_required
def load_class_and_subjects_for_gat(request):
    """
    HTMX: Загружает поля 'Класс (Параллель)' и 'Предметы' для формы GatTest.
    """
    school = None
    school_id = request.GET.get('school')

    if school_id:
        try:
            school = School.objects.get(pk=school_id)
        except School.DoesNotExist:
            pass

    form = GatTestForm(request=request, school=school)
    return render(request, 'gat_tests/partials/_class_and_subjects_fields.html', {'form': form})

@login_required
def load_fields_for_qc(request):
    """
    HTMX: Загружает поля 'Класс (Параллель)' и 'Предмет' для формы QuestionCount.
    """
    school_id = request.GET.get('school')
    school = None

    if school_id:
        try:
            school = School.objects.get(pk=school_id)
        except School.DoesNotExist:
            pass

    form = QuestionCountForm(school=school)
    return render(request, 'question_counts/partials/_dependent_fields.html', {'form': form})

# =============================================================================
# --- API ДЛЯ УВЕДОМЛЕНИЙ ---
# =============================================================================

@login_required
def get_notifications_api(request):
    """ API: Возвращает непрочитанные уведомления пользователя. """
    dushanbe_tz = pytz.timezone("Asia/Dushanbe")
    notifications = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')
    results = []
    for notif in notifications:
        local_time = notif.created_at.astimezone(dushanbe_tz)
        formatted_time = local_time.strftime('%H:%M, %d.%m.%Y')
        results.append({
            'id': notif.id,
            'message': notif.message,
            'link': notif.link or '#',
            'time': formatted_time
        })
    return JsonResponse({'unread_count': notifications.count(), 'notifications': results})

@login_required
def mark_notifications_as_read(request):
    """ API: Помечает все уведомления пользователя как прочитанные. """
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)

# =============================================================================
# --- API ДЛЯ ПОИСКА ---
# =============================================================================

@login_required
def header_search_api(request):
    """ API: Поиск по студентам и тестам в шапке сайта с учетом прав. """
    query = request.GET.get('q', '').strip()
    results = []
    user = request.user

    if query:
        accessible_schools = get_accessible_schools(user)
        students_qs = Student.objects.filter(school_class__school__in=accessible_schools)
        students = students_qs.filter(
            Q(first_name_ru__icontains=query) | Q(last_name_ru__icontains=query) |
            Q(student_id__icontains=query)
        ).select_related('school_class').distinct()[:5]

        for s in students:
            results.append({
                'type': 'Студент',
                'name': f"{s.first_name_ru} {s.last_name_ru} ({s.school_class.name})",
                'url': reverse('core:student_progress', args=[s.id])
            })

        tests_qs = GatTest.objects.filter(school__in=accessible_schools)
        tests = tests_qs.filter(name__icontains=query).select_related('school')[:5]

        for t in tests:
            results.append({
                'type': 'Тест',
                'name': f"{t.name} ({t.school.name})",
                'url': f"{reverse('core:detailed_results_list', args=[t.test_number])}?test_id={t.id}"
            })

    return JsonResponse({'results': results})

# =============================================================================
# --- API ДЛЯ УПРАВЛЕНИЯ ПРАВАМИ ДОСТУПА ---
# =============================================================================

@login_required
def toggle_school_access_api(request):
    """ API для управления доступом Директора к Школе. """
    if request.method == 'POST' and request.user.is_staff:
        director_id = request.POST.get('director_id')
        school_id = request.POST.get('school_id')
        try:
            user_profile = UserProfile.objects.get(user_id=director_id, role='DIRECTOR')
            school = School.objects.get(pk=school_id)
            if user_profile.schools.filter(pk=school.pk).exists():
                user_profile.schools.remove(school)
                return JsonResponse({'status': 'removed'})
            else:
                user_profile.schools.add(school)
                return JsonResponse({'status': 'added'})
        except (UserProfile.DoesNotExist, School.DoesNotExist):
            return JsonResponse({'status': 'error', 'message': 'Объект не найден'}, status=404)
    return JsonResponse({'status': 'error', 'message': 'Неверный запрос'}, status=400)

@login_required
def toggle_subject_access_api(request):
    """ API для управления доступом Эксперта к Предмету. """
    if request.method == 'POST' and request.user.is_staff:
        expert_id = request.POST.get('expert_id')
        subject_id = request.POST.get('subject_id')
        try:
            user_profile = UserProfile.objects.get(user_id=expert_id, role='EXPERT')
            subject = Subject.objects.get(pk=subject_id)
            if user_profile.subjects.filter(pk=subject.pk).exists():
                user_profile.subjects.remove(subject)
                return JsonResponse({'status': 'removed'})
            else:
                user_profile.subjects.add(subject)
                return JsonResponse({'status': 'added'})
        except (UserProfile.DoesNotExist, Subject.DoesNotExist):
            return JsonResponse({'status': 'error', 'message': 'Объект не найден'}, status=404)
    return JsonResponse({'status': 'error', 'message': 'Неверный запрос'}, status=400)

@login_required
def api_load_schools(request):
    """
    API: Загружает школы, в которых проводились тесты в выбранных четвертях.
    Возвращает HTML-фрагмент с "чипами".
    """
    quarter_ids = request.GET.getlist('quarters[]')
    user = request.user
    
    schools_qs = get_accessible_schools(user)
    
    if quarter_ids:
        school_ids_with_tests = GatTest.objects.filter(
            quarter_id__in=quarter_ids
        ).values_list('school_id', flat=True).distinct()
        
        schools_qs = schools_qs.filter(id__in=school_ids_with_tests)
        
    context = {
        'form_field_name': 'schools',
        'items': schools_qs.order_by('name'),
    }
    return render(request, 'partials/_chip_options.html', context)

@login_required
def api_load_subjects_for_user_form(request):
    """
    HTMX: Загружает поле 'Предметы' для формы UserProfileForm.
    (Исправлено: предметы больше не фильтруются по школе)
    """
    school_id = request.GET.get('school')
    
    form = UserProfileForm()
    
    # Предметы больше не зависят от школы
    form.fields['subjects'].queryset = Subject.objects.all().order_by('name')
        
    return render(request, 'accounts/partials/_user_form_subjects.html', {'profile_form': form})