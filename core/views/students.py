# D:\New_GAT\core\views\students.py (ОБНОВЛЕННАЯ ВЕРСИЯ С ПРАВАМИ ДИРЕКТОРА)

# Стандартная библиотека Python
import json
import logging
from collections import defaultdict

import pandas as pd
from django.http import HttpResponse

# Сторонние библиотеки
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.views.generic import CreateView, DeleteView, UpdateView
from weasyprint import HTML

# Локальные импорты
from accounts.models import UserProfile
from .. import utils
from ..forms import StudentForm, StudentUploadForm
from ..models import (
    QuestionCount,
    School,
    SchoolClass,
    Student,
    StudentResult,
    Subject,
)
from ..services import process_student_upload
from .permissions import get_accessible_schools

logger = logging.getLogger('cleanup_logger')

# =============================================================================
# --- МИКСИН ДЛЯ ПРОВЕРКИ ПРАВ ДОСТУПА ---
# =============================================================================

class StudentAccessMixin:
    """
    Миксин для проверки прав Суперпользователя или Директора
    на управление учениками конкретной школы.
    """
    def dispatch(self, request, *args, **kwargs):
        user = request.user
        target_school = None

        # Determine the target school based on view type (Create, Update, Delete)
        # ... (logic to find target_school) ...

        # Check permissions
        is_allowed = False
        if user.is_superuser:
            is_allowed = True
        elif target_school and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.DIRECTOR:
            # <<<--- THIS IS THE KEY CHECK ---<<<
            if target_school in get_accessible_schools(user): # Checks if the school is in the Director's M2M list
                is_allowed = True

        if not is_allowed:
            # Raises PermissionDenied if not superuser or director of that school
            raise PermissionDenied("У вас нет прав для выполнения этого действия с учениками данной школы.")

        return super().dispatch(request, *args, **kwargs)

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПРОВЕРКИ ПРАВ ---
# =============================================================================

def _check_student_account_permission(user, student):
    """Вспомогательная функция для проверки прав на управление аккаунтом ученика."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    if profile and profile.role == UserProfile.Role.DIRECTOR:
        if student.school_class.school in get_accessible_schools(user):
            return True
    return False

def _check_class_or_parallel_permission(user, class_or_parallel):
    """Вспомогательная функция для проверки прав на класс/параллель."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    if profile and profile.role == UserProfile.Role.DIRECTOR:
        if class_or_parallel.school in get_accessible_schools(user):
            return True
    return False

# =============================================================================
# --- ИЕРАРХИЧЕСКИЙ СПИСОК УЧЕНИКОВ ---
# =============================================================================

@login_required
def student_school_list_view(request):
    """Шаг 1: Отображает список школ, доступных пользователю."""
    accessible_schools = get_accessible_schools(request.user)
    
    schools_with_counts = accessible_schools.annotate(
        student_count=Count('classes__students')
    ).order_by('name')

    context = {
        'title': 'Ученики: Выберите школу',
        'schools': schools_with_counts,
    }
    return render(request, 'students/student_school_list.html', context)

@login_required
def student_parallel_list_view(request, school_id):
    """Шаг 2: Отображает список параллелей для выбранной школы."""
    school = get_object_or_404(School, id=school_id)
    
    if school not in get_accessible_schools(request.user):
        messages.error(request, "У вас нет доступа к этой школе.")
        return redirect('core:student_school_list')

    parallels = SchoolClass.objects.filter(school=school, parent__isnull=True)\
                                   .annotate(student_count=Count('subclasses__students'))\
                                   .order_by('name')

    return render(request, 'students/student_parallel_list.html', {
        'title': f'Выберите параллель в "{school.name}"',
        'school': school,
        'parallels': parallels
    })

@login_required
def student_class_list_view(request, parent_id):
    """Шаг 3: Отображает список классов внутри выбранной параллели."""
    parent_class = get_object_or_404(SchoolClass, id=parent_id)
    school = parent_class.school

    if school not in get_accessible_schools(request.user):
        messages.error(request, "У вас нет доступа к этому разделу.")
        return redirect('core:student_school_list')

    classes_queryset = SchoolClass.objects.filter(parent=parent_class)\
                                          .annotate(student_count=Count('students'))\
                                          .order_by('name')
    
    search_query = request.GET.get('q', '')
    if search_query:
        classes_queryset = classes_queryset.filter(name__icontains=search_query)

    context = {
        'title': f'Классы параллели «{parent_class.name}»',
        'school': school,
        'parent_class': parent_class, 
        'classes': classes_queryset,
        'search_query': search_query,
    }

    if request.htmx:
        return render(request, 'students/partials/_class_list.html', context)
        
    return render(request, 'students/student_class_list.html', context)

@login_required
def student_list_view(request, class_id):
    """Шаг 4: Отображает список учеников в конкретном классе."""
    school_class = get_object_or_404(SchoolClass.objects.select_related('school', 'parent'), id=class_id)
    
    if school_class.school not in get_accessible_schools(request.user):
        messages.error(request, "У вас нет доступа к этому классу.")
        return redirect('core:student_school_list')

    student_list = Student.objects.filter(school_class=school_class)\
                                 .select_related('user_profile__user')\
                                 .order_by('last_name_ru', 'first_name_ru')

    context = {
        'title': f'Ученики класса {school_class.name}',
        'school_class': school_class,
        'students': student_list,
    }
    return render(request, 'students/student_list_final.html', context)

# =============================================================================
# --- CRUD ОПЕРАЦИИ (С ПРАВАМИ ДИРЕКТОРА) ---
# =============================================================================

class StudentCreateView(LoginRequiredMixin, StudentAccessMixin, CreateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/student_form.html'
    
    def get_success_url(self):
        return reverse_lazy('core:student_list', kwargs={'class_id': self.object.school_class.id})

    def get_initial(self):
        if class_id := self.request.GET.get('class_id'):
            return {'school_class': class_id}
        return super().get_initial()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Добавить ученика'
        
        class_id = self.request.GET.get('class_id')
        context['class_id'] = class_id

        if class_id:
            context['cancel_url'] = reverse_lazy('core:student_list', kwargs={'class_id': class_id})
        else:
            context['cancel_url'] = reverse_lazy('core:student_school_list')
        return context

    def form_valid(self, form):
        messages.success(self.request, "Ученик успешно добавлен.")
        return super().form_valid(form)

class StudentUpdateView(LoginRequiredMixin, StudentAccessMixin, UpdateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/student_form.html'
    
    def get_success_url(self):
        return reverse_lazy('core:student_list', kwargs={'class_id': self.object.school_class.id})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактировать ученика'
        context['class_id'] = self.object.school_class.id
        context['cancel_url'] = self.get_success_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, "Данные ученика успешно обновлены.")
        return super().form_valid(form)

class StudentDeleteView(LoginRequiredMixin, StudentAccessMixin, DeleteView):
    model = Student
    template_name = 'students/student_confirm_delete.html'

    def get_success_url(self):
        if self.object:
            return reverse_lazy('core:student_list', kwargs={'class_id': self.object.school_class_id})
        return reverse_lazy('core:student_school_list')

    def form_valid(self, form):
        student_name = str(self.object)
        success_url = self.get_success_url()
        self.object.delete()

        if self.request.htmx:
            trigger = {
                "close-delete-modal": True, 
                "show-message": {
                    "text": f"Ученик {student_name} удален.", 
                    "type": "error"
                }
            }
            return HttpResponse(
                status=200, 
                headers={
                    'HX-Trigger': json.dumps(trigger), 
                    'HX-Refresh': 'true'
                }
            )

        messages.success(self.request, f"Ученик {student_name} был успешно удален.")
        return redirect(success_url)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = self.get_success_url()
        return context

@login_required
def student_delete_multiple_view(request):
    """Массовое удаление выбранных учеников."""
    user = request.user

    if request.method == 'POST':
        student_ids_to_delete = request.POST.getlist('student_ids')
        class_id = request.POST.get('class_id')

        # Проверка прав
        target_school = None
        if class_id:
            try:
                target_class = SchoolClass.objects.select_related('school').get(pk=class_id)
                target_school = target_class.school
            except SchoolClass.DoesNotExist:
                messages.error(request, "Указанный класс не найден.")
                return redirect('core:student_school_list')

        is_allowed = False
        if user.is_superuser:
            is_allowed = True
        elif target_school and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.DIRECTOR:
            if target_school in get_accessible_schools(user):
                is_allowed = True

        if not is_allowed:
            messages.error(request, "У вас нет прав для удаления учеников из этого класса.")
            return redirect('core:student_school_list')

        if not student_ids_to_delete:
            messages.warning(request, "Вы не выбрали ни одного ученика для удаления.")
        else:
            students_query = Student.objects.filter(pk__in=student_ids_to_delete, school_class__school=target_school)
            deleted_count, _ = students_query.delete()
            messages.success(request, f"Успешно удалено учеников: {deleted_count}.")

        if class_id:
            return redirect('core:student_list', class_id=class_id)

    return redirect('core:student_school_list')

# =============================================================================
# --- МАССОВЫЕ ОПЕРАЦИИ И УПРАВЛЕНИЕ АККАУНТАМИ (С ПРАВАМИ) ---
# =============================================================================

@login_required
def student_upload_view(request):
    """Обрабатывает загрузку учеников из Excel файла."""
    if not request.user.is_superuser:
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect('core:student_school_list')

    if request.method == 'POST':
        form = StudentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES['file']
            try:
                report = process_student_upload(file)

                row_errors = report.get('errors', [])
                if row_errors:
                    for error_message in row_errors:
                        messages.warning(request, error_message)

                created_count = report.get('created', 0)
                updated_count = report.get('updated', 0)
                total_processed = created_count + updated_count

                if total_processed > 0:
                    messages.success(
                        request,
                        f"Операция завершена. Всего обработано: {total_processed} (Создано: {created_count}, Обновлено: {updated_count})."
                    )
                elif not row_errors:
                    messages.info(
                        request,
                        "Файл обработан, но не найдено новых учеников для добавления или обновления."
                    )
                
            except Exception as e:
                messages.error(request, f"Произошла критическая ошибка при обработке файла: {e}")
                return redirect('core:student_upload')
            
            return redirect('core:student_school_list')
        else:
            messages.error(request, "Ошибка в форме. Пожалуйста, прикрепите корректный файл.")

    form = StudentUploadForm()
    context = {
        'title': 'Загрузить учеников из Excel',
        'form': form
    }
    return render(request, 'students/student_upload_form.html', context)

@login_required
@transaction.atomic
def create_student_user_account(request, student_id):
    """Создает аккаунт для отдельного ученика"""
    student = get_object_or_404(Student.objects.select_related('school_class__school'), id=student_id)
    redirect_url = reverse_lazy('core:student_list', kwargs={'class_id': student.school_class_id})

    # Проверка прав
    if not _check_student_account_permission(request.user, student):
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect(redirect_url)

    if request.method == 'POST':
        if hasattr(student, 'user_profile') and student.user_profile:
            messages.warning(request, f"У ученика {student} уже есть аккаунт.")
            return redirect(redirect_url)

        try:
            first_name = student.first_name_en or ''
            last_name = student.last_name_en or ''
            base_username = f"{first_name}{last_name}" if first_name or last_name else student.student_id
            base_username = ''.join(e for e in base_username if e.isalnum()).lower()
            
            username = base_username or 'student'
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            
            password = get_random_string(length=8)
            
            user = User.objects.create_user(
                username=username, 
                password=password, 
                first_name=student.first_name_ru, 
                last_name=student.last_name_ru
            )
            
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.role = UserProfile.Role.STUDENT
            profile.student = student
            profile.save()

            messages.success(
                request, 
                f"Аккаунт для {student} успешно создан. Логин: {username}, Пароль: {password}"
            )
            
        except Exception as e:
            messages.error(request, f"Ошибка при создании аккаунта: {e}")
            
        return redirect(redirect_url)
    
    return redirect(redirect_url)

@login_required
def student_reset_password(request, user_id):
    """Сброс пароля ученика с поддержкой HTMX."""
    user_to_reset = get_object_or_404(User, id=user_id)
    student = get_object_or_404(Student.objects.select_related('school_class__school'), user_profile__user=user_to_reset)
    redirect_url = reverse_lazy('core:student_list', kwargs={'class_id': student.school_class_id})

    # Проверка прав
    if not _check_student_account_permission(request.user, student):
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect(redirect_url)

    if request.method == 'POST':
        new_password = get_random_string(length=8)
        user_to_reset.set_password(new_password)
        user_to_reset.save()

        if request.htmx:
            context = {
                'student': student,
                'new_password': new_password
            }
            html = render_to_string('students/partials/_reset_password_result.html', context, request=request)
            
            trigger = {
                "show-message": {
                    "text": f"Пароль для {user_to_reset.username} сброшен.",
                    "type": "success"
                }
            }
            headers = {'HX-Trigger': json.dumps(trigger)}
            
            return HttpResponse(html, headers=headers)
        else:
            messages.success(
                request,
                f"Пароль для {user_to_reset.username} сброшен. Новый пароль: {new_password}"
            )
            return redirect(redirect_url)
    
    return redirect(redirect_url)

@login_required
@transaction.atomic
def class_create_export_accounts(request, class_id):
    """Массовое создание/сброс и экспорт аккаунтов для КЛАССА."""
    school_class = get_object_or_404(SchoolClass.objects.select_related('school'), id=class_id)
    redirect_url = reverse_lazy('core:student_list', kwargs={'class_id': class_id})

    # Проверка прав
    if not _check_class_or_parallel_permission(request.user, school_class):
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect(redirect_url)
    
    action = request.POST.get('action')
    credentials_list = []
    
    # 1. Обрабатываем учеников, у которых уже есть аккаунт
    students_with_accounts = Student.objects.filter(
        school_class=school_class, 
        user_profile__isnull=False
    ).select_related('user_profile__user')

    reset_count = 0
    for student in students_with_accounts:
        user = student.user_profile.user
        password_to_show = '(пароль установлен)'

        if action == 'reset_and_export':
            new_password = get_random_string(length=8)
            user.set_password(new_password)
            user.save()
            password_to_show = new_password
            reset_count += 1
        
        credentials_list.append({
            'full_name': student.full_name_ru, 
            'username': user.username, 
            'password': password_to_show
        })

    # 2. Создаем аккаунты для новых учеников
    students_to_create = Student.objects.filter(
        school_class=school_class, 
        user_profile__isnull=True
    )
    
    created_count = 0
    for student in students_to_create:
        first_name = student.first_name_en or ''
        last_name = student.last_name_en or ''
        base_username = f"{first_name}{last_name}" if first_name or last_name else student.student_id
        base_username = ''.join(e for e in base_username if e.isalnum()).lower()
        username = base_username or 'student'
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        password = get_random_string(length=8)
        user = User.objects.create_user(
            username=username, password=password, 
            first_name=student.first_name_ru, last_name=student.last_name_ru
        )
        profile = user.profile
        profile.role = UserProfile.Role.STUDENT
        profile.student = student
        profile.save()
        credentials_list.append({
            'full_name': student.full_name_ru, 
            'username': username, 
            'password': password
        })
        created_count += 1

    # 3. Генерируем PDF
    if not credentials_list:
        messages.warning(request, "В этом классе нет учеников для экспорта.")
        return redirect(redirect_url)
        
    credentials_list.sort(key=lambda x: x['full_name'])
    
    context = {'credentials': credentials_list, 'school_class': school_class}
    html_string = render_to_string('students/logins_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="logins_{school_class.name}.pdf"'
    HTML(string=html_string).write_pdf(response)
    
    message_parts = []
    if created_count > 0:
        message_parts.append(f"Создано {created_count} новых аккаунтов")
    if reset_count > 0:
        message_parts.append(f"сброшен пароль для {reset_count} существующих")
    
    final_message = ", ".join(message_parts).capitalize() + ". PDF-файл с актуальными данными сгенерирован."
    messages.success(request, final_message)
        
    return response

@login_required
@transaction.atomic
def delete_student_user_account(request, user_id):
    """Удаляет объект User, связанный с учеником"""
    user_to_delete = get_object_or_404(User, id=user_id)
    student = get_object_or_404(Student.objects.select_related('school_class__school'), user_profile__user=user_to_delete)
    redirect_url = reverse_lazy('core:student_list', kwargs={'class_id': student.school_class_id})

    # Проверка прав
    if not _check_student_account_permission(request.user, student):
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect(redirect_url)

    if request.method == 'POST':
        username = user_to_delete.username
        user_to_delete.delete()
        messages.success(request, f"Аккаунт пользователя '{username}' был успешно удален.")
        return redirect(redirect_url)

    return redirect(redirect_url)

# =============================================================================
# --- АНАЛИТИКА ПРОГРЕССА СТУДЕНТА ---
# =============================================================================

@login_required
def student_progress_view(request, student_id):
    """Детальная аналитика прогресса ученика"""
    student = get_object_or_404(
        Student.objects.select_related('school_class__school').prefetch_related('notes__author'), 
        id=student_id
    )
    
    if not request.user.is_superuser and student.school_class.school not in get_accessible_schools(request.user):
        messages.error(request, "У вас нет доступа к данным этого ученика.")
        return redirect('core:student_school_list')
    
    student_results_qs = student.results.select_related('gat_test__quarter__year').order_by('-gat_test__test_date')

    if not student_results_qs.exists():
        return render(request, 'students/student_progress.html', {
            'title': f'Аналитика: {student}', 
            'student': student, 
            'notes': student.notes.all(), 
            'has_results': False
        })

    test_ids = student_results_qs.values_list('gat_test_id', flat=True)
    all_results_for_tests = StudentResult.objects.filter(
        gat_test_id__in=test_ids
    ).select_related('student__school_class__school')
    
    scores_by_test, scores_by_class, scores_by_school = defaultdict(list), defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
    
    for res in all_results_for_tests:
        scores_by_test[res.gat_test_id].append(res.total_score)
        scores_by_class[res.gat_test_id][res.student.school_class_id].append(res.total_score)
        scores_by_school[res.gat_test_id][res.student.school_class.school_id].append(res.total_score)
    
    for test_id in scores_by_test:
        scores_by_test[test_id].sort(reverse=True)
        for class_id in scores_by_class[test_id]: 
            scores_by_class[test_id][class_id].sort(reverse=True)
        for school_id in scores_by_school[test_id]: 
            scores_by_school[test_id][school_id].sort(reverse=True)
    
    subject_map = {s.id: s.name for s in Subject.objects.all()}
    detailed_results_data = []
    
    for result in student_results_qs:
        student_score = result.total_score
        class_scores = scores_by_class.get(result.gat_test_id, {}).get(student.school_class_id, [])
        school_scores = scores_by_school.get(result.gat_test_id, {}).get(student.school_class.school_id, [])
        parallel_scores = scores_by_test.get(result.gat_test_id, [])

        try: 
            class_rank = class_scores.index(student_score) + 1
        except ValueError: 
            class_rank = None
        try: 
            school_rank = school_scores.index(student_score) + 1
        except ValueError: 
            school_rank = None
        try: 
            parallel_rank = parallel_scores.index(student_score) + 1
        except ValueError: 
            parallel_rank = None

        grade, best_s, worst_s, processed_scores = _get_grade_and_subjects_performance(result, subject_map)
        
        detailed_results_data.append({
            'result': result, 
            'class_rank': class_rank, 
            'class_total': len(class_scores),
            'parallel_rank': parallel_rank, 
            'parallel_total': len(parallel_scores),
            'school_rank': school_rank, 
            'school_total': len(school_scores),
            'grade': grade, 
            'best_subject': best_s, 
            'worst_subject': worst_s, 
            'processed_scores': processed_scores
        })
        
    comparison_data = None
    if len(detailed_results_data) >= 2:
        latest, previous = detailed_results_data[0], detailed_results_data[1]
        grade_diff = (
            latest.get('grade') - previous.get('grade') 
            if latest.get('grade') is not None and previous.get('grade') is not None 
            else None
        )
        rank_diff = (
            previous.get('class_rank') - latest.get('class_rank') 
            if all([previous.get('class_rank'), latest.get('class_rank')]) 
            else None
        )
        comparison_data = {
            'latest': latest, 
            'previous': previous, 
            'grade_diff': grade_diff, 
            'rank_diff': rank_diff
        }
    
    context = {
        'title': f'Аналитика: {student}', 
        'student': student, 
        'detailed_results_data': detailed_results_data, 
        'comparison_data': comparison_data, 
        'notes': student.notes.all(), 
        'has_results': True
    }
    return render(request, 'students/student_progress.html', context)

def _get_grade_and_subjects_performance(result, subject_map):
    student_class = result.student.school_class
    parent_class = student_class.parent if student_class.parent else student_class

    q_counts_parallel = {
        qc.subject_id: qc.number_of_questions
        for qc in QuestionCount.objects.filter(school_class=parent_class)
    }

    total_student_score = 0
    total_max_score = 0
    subject_performance = []
    processed_scores = [] # <-- Эта переменная будет заполнена и возвращена

    if isinstance(result.scores_by_subject, dict):
        for subj_id_str, answers_dict in result.scores_by_subject.items():
            try:
                subj_id = int(subj_id_str)
                subject_name = subject_map.get(subj_id)
                q_count_for_subject = q_counts_parallel.get(subj_id, 0)

                if subject_name and isinstance(answers_dict, dict):
                    correct_q = sum(1 for answer in answers_dict.values() if answer is True)
                    total_q = len(answers_dict) # Используем длину словаря для total

                    total_student_score += correct_q
                    total_max_score += q_count_for_subject

                    if q_count_for_subject > 0:
                        perf = (correct_q / q_count_for_subject) * 100
                        subject_performance.append({'name': subject_name, 'perf': perf})

                        # --- ✨ ДОБАВЛЕН РАСЧЕТ ОЦЕНКИ ПО ПРЕДМЕТУ ✨ ---
                        grade_for_subject = utils.calculate_grade_from_percentage(perf)
                        # --- ✨ КОНЕЦ ДОБАВЛЕНИЯ ✨ ---

                        # --- ✨ ДОБАВЛЕНА СОРТИРОВКА ОТВЕТОВ ПО НОМЕРУ ВОПРОСА ✨ ---
                        # Превращаем {'10': False, '1': True, '2': True}
                        # в [('1', True), ('2', True), ('10', False)]
                        sorted_answers = sorted(
                            answers_dict.items(),
                            key=lambda item: int(item[0]) # Сортируем по числовому значению ключа
                        )
                        # --- ✨ КОНЕЦ СОРТИРОВКИ ✨ ---

                        # Добавляем все нужные данные в processed_scores
                        processed_scores.append({
                            'subject': subject_name,
                            'answers': sorted_answers, # <-- Передаем отсортированный СПИСОК ПАР
                            'correct': correct_q,
                            'total': total_q, # Передаем реальное кол-во ответов
                            'max_possible': q_count_for_subject, # Макс. балл
                            'incorrect': total_q - correct_q,
                            'percentage': round(perf, 1),
                            'grade': grade_for_subject # <-- Добавили оценку
                        })
            except (ValueError, TypeError):
                continue

    overall_percentage = (total_student_score / total_max_score) * 100 if total_max_score > 0 else 0
    grade = utils.calculate_grade_from_percentage(overall_percentage)

    best_subject = max(subject_performance, key=lambda x: x['perf']) if subject_performance else None
    worst_subject = min(subject_performance, key=lambda x: x['perf']) if subject_performance else None

    # Возвращаем заполненный processed_scores
    return grade, best_subject, worst_subject, processed_scores

# =============================================================================
# --- ОЧИСТКА ДАННЫХ (ТОЛЬКО ДЛЯ СУПЕРПОЛЬЗОВАТЕЛЯ) ---
# =============================================================================

@login_required
def data_cleanup_view(request):
    """Страница для массового удаления данных"""
    if not request.user.is_superuser:
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect('core:student_school_list')

    if request.method == 'POST':
        user = request.user

        if 'delete_students_parallel' in request.POST:
            parallel_id = request.POST.get('parallel_id')
            if parallel_id:
                parallel = get_object_or_404(SchoolClass, pk=parallel_id)
                students_to_delete = Student.objects.filter(school_class__parent_id=parallel_id)
                deleted_count, _ = students_to_delete.delete()
                
                logger.critical(f"USER: '{user.username}' удалил {deleted_count} УЧЕНИКОВ из параллели '{parallel.name}'.")
                messages.warning(request, f'ВНИМАНИЕ: Удалено {deleted_count} учеников из параллели "{parallel.name}".')
            else:
                messages.error(request, 'Вы не выбрали параллель для удаления учеников.')

        elif 'clear_results_class' in request.POST:
            class_id = request.POST.get('class_id')
            if class_id:
                school_class = SchoolClass.objects.get(pk=class_id)
                class_name = school_class.name
                deleted_count, _ = StudentResult.objects.filter(student__school_class_id=class_id).delete()
                
                logger.warning(f"USER: '{user.username}' удалил {deleted_count} РЕЗУЛЬТАТОВ ТЕСТОВ для класса '{class_name}'.")
                messages.success(request, f'Успешно удалено {deleted_count} записей для класса "{class_name}".')
            else:
                messages.error(request, 'Вы не выбрали класс для очистки результатов.')

        elif 'clear_results_all' in request.POST:
            deleted_count, _ = StudentResult.objects.all().delete()
            
            logger.warning(f"USER: '{user.username}' удалил ВСЕ ({deleted_count}) РЕЗУЛЬТАТЫ ТЕСТОВ в системе.")
            messages.success(request, f'ПОЛНАЯ ОЧИСТКА РЕЗУЛЬТАТОВ ЗАВЕРШЕНА. Удалено {deleted_count} записей.')

        elif 'delete_students_class' in request.POST:
            class_id = request.POST.get('class_id')
            if class_id:
                school_class = SchoolClass.objects.get(pk=class_id)
                class_name = school_class.name
                deleted_count, _ = Student.objects.filter(school_class_id=class_id).delete()
                
                logger.critical(f"USER: '{user.username}' удалил {deleted_count} УЧЕНИКОВ из класса '{class_name}'.")
                messages.warning(request, f'ВНИМАНИЕ: Удалено {deleted_count} учеников из класса "{class_name}".')
            else:
                messages.error(request, 'Вы не выбрали класс для удаления учеников.')

        elif 'delete_students_all' in request.POST:
            deleted_count, _ = Student.objects.all().delete()
            
            logger.critical(f"USER: '{user.username}' удалил ВСЕХ ({deleted_count}) УЧЕНИКОВ в системе.")
            messages.warning(request, f'ВНИМАНИЕ: ВСЕ УЧЕНИКИ В СИСТЕМЕ ({deleted_count}) БЫЛИ УДАЛЕНЫ.')

        return redirect('core:data_cleanup')

    classes = SchoolClass.objects.select_related('school').order_by('school__name', 'name')
    parallels = SchoolClass.objects.filter(parent__isnull=True).select_related('school').order_by('school__name', 'name')
    
    context = {
        'title': 'Очистка и управление данными',
        'classes': classes,
        'parallels': parallels,
    }
    return render(request, 'students/data_cleanup.html', context)

# =============================================================================
# --- ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ---
# =============================================================================

@login_required
def student_list_combined_view(request, parallel_id):
    """Отображает ВСЕХ учеников в параллели."""
    parallel = get_object_or_404(SchoolClass.objects.select_related('school'), id=parallel_id, parent__isnull=True)
    
    if parallel.school not in get_accessible_schools(request.user):
        messages.error(request, "У вас нет доступа к этому разделу.")
        return redirect('core:student_school_list')

    student_list = Student.objects.filter(school_class__parent=parallel)\
                                 .select_related('user_profile__user', 'school_class')\
                                 .order_by('school_class__name', 'last_name_ru', 'first_name_ru')

    context = {
        'title': f'Все ученики параллели «{parallel.name}»',
        'school_class': parallel,
        'students': student_list,
        'is_combined_view': True,
    }
    return render(request, 'students/student_list_final.html', context)

@login_required
@transaction.atomic
def parallel_create_export_accounts(request, parallel_id):
    """Массовое создание/сброс и экспорт аккаунтов для ВСЕЙ ПАРАЛЛЕЛИ."""
    parallel = get_object_or_404(SchoolClass.objects.select_related('school'), id=parallel_id, parent__isnull=True)
    redirect_url = reverse_lazy('core:student_list_combined', kwargs={'parallel_id': parallel_id})

    # Проверка прав
    if not _check_class_or_parallel_permission(request.user, parallel):
        messages.error(request, "У вас нет прав для выполнения этого действия.")
        return redirect(redirect_url)
    
    action = request.POST.get('action')
    students_in_parallel = Student.objects.filter(school_class__parent=parallel)
    
    credentials_list = []
    reset_count = 0
    created_count = 0

    # 1. Обрабатываем учеников, у которых уже есть аккаунт
    students_with_accounts = students_in_parallel.filter(
        user_profile__isnull=False
    ).select_related('user_profile__user')

    for student in students_with_accounts:
        user = student.user_profile.user
        password_to_show = '(пароль установлен)'

        if action == 'reset_and_export':
            new_password = get_random_string(length=8)
            user.set_password(new_password)
            user.save()
            password_to_show = new_password
            reset_count += 1
        
        credentials_list.append({
            'full_name': student.full_name_ru, 
            'username': user.username, 
            'password': password_to_show
        })

    # 2. Создаем аккаунты для новых учеников
    students_to_create = students_in_parallel.filter(user_profile__isnull=True)
    
    for student in students_to_create:
        first_name = student.first_name_en or ''
        last_name = student.last_name_en or ''
        base_username = f"{first_name}{last_name}" if first_name or last_name else student.student_id
        base_username = ''.join(e for e in base_username if e.isalnum()).lower()
        username = base_username or 'student'
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        password = get_random_string(length=8)
        user = User.objects.create_user(
            username=username, password=password, 
            first_name=student.first_name_ru, last_name=student.last_name_ru
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.Role.STUDENT
        profile.student = student
        profile.save()
        credentials_list.append({
            'full_name': student.full_name_ru, 
            'username': username, 
            'password': password
        })
        created_count += 1

    # 3. Генерируем PDF
    if not credentials_list:
        messages.warning(request, "В этой параллели нет учеников для экспорта.")
        return redirect(redirect_url)
        
    credentials_list.sort(key=lambda x: x['full_name'])
    
    context = {'credentials': credentials_list, 'school_class': parallel}
    html_string = render_to_string('students/logins_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="logins_parallel_{parallel.name}.pdf"'
    HTML(string=html_string).write_pdf(response)
    
    message_parts = []
    if created_count > 0:
        message_parts.append(f"Создано {created_count} новых аккаунтов")
    if reset_count > 0:
        message_parts.append(f"сброшен пароль для {reset_count} существующих")
    
    final_message = ", ".join(message_parts).capitalize() + ". PDF-файл с актуальными данными сгенерирован."
    if message_parts:
        messages.success(request, final_message)
        
    return response


@login_required
def export_students_excel(request, class_id):
    """
    Экспортирует список учеников в Excel с нумерацией, школой и классом.
    """
    school_class = get_object_or_404(SchoolClass, pk=class_id)
    
    # Проверка прав (опционально)
    if not request.user.is_superuser and not request.user.profile.is_director:
        pass # Тут можно добавить проверку, если нужно

    # Получаем учеников
    students = Student.objects.filter(school_class=school_class).select_related(
        'school_class',             # Подгружаем класс
        'school_class__school',     # Подгружаем школу (для названия)
        'user_profile__user'        # Подгружаем пользователя (для логина)
    ).order_by('last_name_ru', 'first_name_ru')

    data = []
    # Используем enumerate(..., start=1) для нумерации (1, 2, 3...)
    for index, s in enumerate(students, start=1):
        
        username = s.user_profile.user.username if hasattr(s, 'user_profile') and s.user_profile.user else ''
        
        data.append({
            '№': index,                             # 1. Нумерация
            'Школа': s.school_class.school.name,    # 3. Название школы
            'Класс': s.school_class.name,           # 2. Название класса
            'ID': s.student_id,
            'Фамилия (RU)': s.last_name_ru,
            'Имя (RU)': s.first_name_ru,
            'Насаб (TJ)': s.last_name_tj,
            'Ном (TJ)': s.first_name_tj,
            'Surname (EN)': s.last_name_en,
            'Name (EN)': s.first_name_en,
            'Логин': username,
            'Статус': s.get_status_display()
        })

    # Создаем DataFrame
    df = pd.DataFrame(data)

    # Настройка ответа
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    
    # Формируем имя файла: Школа_Класс_Students.xlsx
    filename = f"{school_class.school.name}_{school_class.name}_Students.xlsx"
    
    # Кодировка имени файла для браузера
    import urllib.parse
    quoted_filename = urllib.parse.quote(filename)
    response['Content-Disposition'] = f'attachment; filename="{quoted_filename}"; filename*=UTF-8\'\'{quoted_filename}'

    # Запись Excel с авто-шириной колонок
    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Students')
        
        # Делаем красиво: настраиваем ширину колонок
        worksheet = writer.sheets['Students']
        for idx, col in enumerate(df.columns):
            # Вычисляем максимальную длину содержимого в колонке
            max_len = max((df[col].astype(str).map(len).max(), len(str(col)))) + 2
            # Ставим ширину (но не больше 50 символов, чтобы не было гигантских колонок)
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)

    return response