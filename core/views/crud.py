# D:\New_GAT\core\views\crud.py (ПОЛНАЯ ОБНОВЛЕННАЯ ВЕРСИЯ)

import json
from collections import defaultdict
from django.http import HttpResponse
from django.db.models import Count
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.generic import FormView, ListView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.urls import reverse_lazy, reverse
from django.template.loader import render_to_string
from core.forms import GatTestForm
from core.models import School, SchoolClass, Subject
from core.forms import QuestionCountForm
from accounts.models import UserProfile

# АБСОЛЮТНЫЕ ИМПОРТЫ
from core.models import (
    AcademicYear, Quarter, School, SchoolClass, Subject, GatTest, TeacherNote, QuestionCount
)
from core.forms import (
    AcademicYearForm, QuarterForm, SchoolForm, SchoolClassForm,
    SubjectForm, GatTestForm, TeacherNoteForm, QuestionCountForm,
    QuestionCountBulkSchoolForm
)
from core.views.permissions import get_accessible_schools

# =============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И СЛОВАРИ ---
# =============================================================================
VIEW_MAP = {}

def get_list_view_instance(model_name, request):
    """
    Возвращает экземпляр ListView для указанной модели.
    Используется для получения queryset с учётом прав доступа.
    """
    # ✨ ВАЖНО: Убедись, что импорт ниже правильный.
    # Если твои ListView'ы в этом же файле, 'from .crud' может быть неверным.
    # Судя по твоему коду, они в этом же файле, так что импорт не нужен.
    # from .crud import AcademicYearListView, QuarterListView, SchoolListView, SchoolClassListView, SubjectListView
    
    # Вместо этого:
    global_views = globals()

    if not VIEW_MAP:
        VIEW_MAP.update({
            'AcademicYear': global_views.get('AcademicYearListView'),
            'Quarter': global_views.get('QuarterListView'),
            'School': global_views.get('SchoolListView'),
            'SchoolClass': global_views.get('SchoolClassListView'),
            'Subject': global_views.get('SubjectListView'),
        })
    view_class = VIEW_MAP.get(model_name)
    if view_class:
        instance = view_class()
        instance.request = request
        return instance
    return None

def _get_question_count_htmx_response(request, school, success_message, message_type='success', is_delete=False):
    """
    Вспомогательная функция: генерирует HTMX-ответ для CRUD-операций с QuestionCount.
    Возвращает ОБНОВЛЕННУЮ КАРТОЧКУ ШКОЛЫ целиком.
    """
    # Перезагружаем данные для школы (с сортировкой!)
    all_qcs = QuestionCount.objects.filter(
        school_class__school=school
    ).select_related('subject', 'school_class').order_by('school_class__name', 'subject__name')
    
    school.all_question_counts = all_qcs

    modal_event = "close-delete-modal" if is_delete else "close-modal"
    trigger = {
        modal_event: True,
        "show-message": {"text": success_message, "type": message_type}
    }
    
    # ✨ ВАЖНО: Теперь мы целимся в контейнер всей школы
    target_id = f"#school-card-{school.id}" 
    
    headers = {
        'HX-Trigger': json.dumps(trigger),
        'HX-Retarget': target_id,
        'HX-Reswap': 'innerHTML' # Заменяем содержимое контейнера школы
    }

    # ✨ Рендерим карточку целиком
    html = render_to_string('question_counts/_school_card.html', {'school': school, 'user': request.user}, request=request)
    return HttpResponse(html, headers=headers)

# =============================================================================
# --- БАЗОВЫЕ КЛАССЫ С ЛОГИКОЙ HTMX ---
# =============================================================================
class HtmxListView(LoginRequiredMixin, ListView):
    template_name_prefix = None
    
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_staff and not user.is_superuser:
            if hasattr(self.model, 'school'):
                accessible_schools = get_accessible_schools(user)
                qs = qs.filter(school__in=accessible_schools)
            elif hasattr(self.model, 'school_class'):
                accessible_schools = get_accessible_schools(user)
                qs = qs.filter(school_class__school__in=accessible_schools)
        return qs

    def get_template_names(self):
        if self.request.htmx:
            return [f'{self.template_name_prefix}/_table.html']
        return [f'{self.template_name_prefix}/list.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'object_list' in context:
            context['items'] = context.pop('object_list')
        return context

class HtmxCreateView(LoginRequiredMixin, CreateView):
    template_name_prefix = None
    list_url_name = None

    def get_template_names(self):
        if self.request.htmx:
            return [f'{self.template_name_prefix}/form_modal.html']
        return [f'{self.template_name_prefix}/form.html']

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно создан.'
        messages.success(self.request, success_message)

        if self.request.htmx:
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            
            list_view = get_list_view_instance(self.model.__name__, self.request)
            if list_view:
                context = {'items': list_view.get_queryset(), **list_view.extra_context}
                html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
                return HttpResponse(html, headers=headers)
            else:
                # Fallback, если list_view не найден (для SchoolClass и др.)
                return HttpResponse(status=204, headers=headers)


        return redirect(reverse_lazy(self.list_url_name))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Добавить: {self.model._meta.verbose_name}'
        context['cancel_url'] = reverse_lazy(self.list_url_name)
        return context
    
    def form_invalid(self, form):
        if self.request.htmx:
            response = render(self.request, f'{self.template_name_prefix}/partials/_form_content.html', self.get_context_data(form=form))
        return super().form_invalid(form)

class HtmxFormView(LoginRequiredMixin, FormView):
    template_name_prefix = None
    list_url_name = None

    def get_template_names(self):
        if self.request.htmx:
            return [f'{self.template_name_prefix}/form_modal.html']
        return [f'{self.template_name_prefix}/form.html']

    def form_valid(self, form):
        raise NotImplementedError("You must implement form_valid in a subclass.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = "Форма"
        context['cancel_url'] = reverse_lazy(self.list_url_name)
        return context
    
    def form_invalid(self, form):
        if self.request.htmx:
            return render(self.request, self.get_template_names()[0], self.get_context_data(form=form))
        return super().form_invalid(form)

class HtmxUpdateView(LoginRequiredMixin, UpdateView):
    template_name_prefix = None
    list_url_name = None

    def get_template_names(self):
        if self.request.htmx:
            return [f'{self.template_name_prefix}/form_modal.html']
        return [f'{self.template_name_prefix}/form.html']

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно обновлен.'
        messages.success(self.request, success_message)
        
        if self.request.htmx:
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            
            list_view = get_list_view_instance(self.model.__name__, self.request)
            if list_view:
                context = {'items': list_view.get_queryset(), **list_view.extra_context}
                html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
                return HttpResponse(html, headers=headers)
            else:
                # Fallback
                return HttpResponse(status=204, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Редактировать: {self.object}'
        context['cancel_url'] = reverse_lazy(self.list_url_name)
        return context

    def form_invalid(self, form):
        if self.request.htmx:
            response = render(self.request, f'{self.template_name_prefix}/partials/_form_content.html', self.get_context_data(form=form))
        return super().form_invalid(form)

class HtmxDeleteView(LoginRequiredMixin, DeleteView):
    template_name_prefix = None
    list_url_name = None

    def get_success_url(self):
        return reverse_lazy(self.list_url_name)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Удалить: {self.object}'
        context['cancel_url'] = reverse_lazy(self.list_url_name)
        return context

    def post(self, request, *args, **kwargs):
        if self.request.htmx:
            self.object = self.get_object()
            item_name = str(self.object)
            model_name = self.object.__class__.__name__
            self.object.delete()
            success_message = f'"{item_name}" успешно удален.'
            messages.error(self.request, success_message)

            trigger = {"close-delete-modal": True, "show-message": {"text": success_message, "type": "error"}}
            headers = {'HX-Trigger': json.dumps(trigger)}

            list_view = get_list_view_instance(model_name, self.request)
            if list_view:
                context = {'items': list_view.get_queryset(), **list_view.extra_context}
                html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
                return HttpResponse(html, headers=headers)
            else:
                # Fallback
                return HttpResponse(status=204, headers=headers)
        
        return super().post(request, *args, **kwargs)

# =============================================================================
# --- УПРАВЛЕНИЕ (MANAGEMENT) ---
# =============================================================================
@login_required
def management_dashboard_view(request):
    return render(request, 'management.html')

# =============================================================================
# --- УЧЕБНЫЕ ГОДЫ (ACADEMIC YEAR) ---
# =============================================================================
class AcademicYearListView(HtmxListView):
    model = AcademicYear
    template_name_prefix = 'years'
    extra_context = {
        'title': 'Учебные годы', 
        'add_url': 'core:year_add',
        'edit_url': 'core:year_edit',
        'delete_url': 'core:year_delete'
    }

class AcademicYearCreateView(HtmxCreateView):
    model = AcademicYear
    form_class = AcademicYearForm
    template_name_prefix = 'years'
    list_url_name = 'core:year_list'

class AcademicYearUpdateView(HtmxUpdateView):
    model = AcademicYear
    form_class = AcademicYearForm
    template_name_prefix = 'years'
    list_url_name = 'core:year_list'

class AcademicYearDeleteView(HtmxDeleteView):
    model = AcademicYear
    template_name = 'years/confirm_delete.html'
    template_name_prefix = 'years'
    list_url_name = 'core:year_list'

# =============================================================================
# --- ЧЕТВЕРТИ (QUARTER) ---
# =============================================================================
class QuarterListView(HtmxListView):
    model = Quarter
    template_name_prefix = 'quarters'
    extra_context = {
        'title': 'Четверти',
        'add_url': 'core:quarter_add',
        'edit_url': 'core:quarter_edit',
        'delete_url': 'core:quarter_delete'
    }

    def get_queryset(self):
        # 1. Получаем базовый запрос
        qs = super().get_queryset()
        
        # 2. Проверяем, нажат ли переключатель "Показать архив"
        # Если галочка стоит, в GET придет параметр show_all='true'
        show_all = self.request.GET.get('show_all') == 'true'

        # 3. Если переключатель ВЫКЛЮЧЕН (по умолчанию), фильтруем
        if not show_all:
            today = timezone.now().date()
            
            # Находим текущий учебный год
            current_academic_year = AcademicYear.objects.filter(
                start_date__lte=today, 
                end_date__gte=today
            ).first()

            if current_academic_year:
                # Показываем четверти ТОЛЬКО текущего года
                qs = qs.filter(year=current_academic_year)
            else:
                # (Опционально) Если сейчас лето и учебный год не идет,
                # можно показать последний добавленный год или ничего.
                # Покажем последние 4 четверти, чтобы список не был пустым.
                qs = qs.order_by('-end_date')[:4]

        # 4. Возвращаем отсортированный список (сначала новые)
        return qs.select_related('year').order_by('-year__start_date', 'start_date')

class QuarterCreateView(HtmxCreateView):
    model = Quarter
    form_class = QuarterForm
    template_name_prefix = 'quarters'
    list_url_name = 'core:quarter_list'

class QuarterUpdateView(HtmxUpdateView):
    model = Quarter
    form_class = QuarterForm
    template_name_prefix = 'quarters'
    list_url_name = 'core:quarter_list'

class QuarterDeleteView(HtmxDeleteView):
    model = Quarter
    template_name = 'quarters/confirm_delete.html'
    template_name_prefix = 'quarters'
    list_url_name = 'core:quarter_list'

# =============================================================================
# --- ШКОЛЫ (SCHOOL) ---
# =============================================================================
class SchoolListView(HtmxListView):
    model = School
    template_name_prefix = 'schools'
    extra_context = {
        'title': 'Школы',
        'add_url': 'core:school_add',
        'edit_url': 'core:school_edit',
        'delete_url': 'core:school_delete',
        'table_template': 'schools/_table.html'
    }

    def get_queryset(self):
        user = self.request.user
        profile = getattr(user, 'profile', None)

        if user.is_superuser:
            queryset = School.objects.all()
        elif profile and profile.role == UserProfile.Role.DIRECTOR:
            queryset = profile.schools.all()
        else:
            queryset = get_accessible_schools(user)

        sort_by = self.request.GET.get('sort', 'school_id')
        direction = self.request.GET.get('direction', 'asc')

        allowed_sort_fields = ['school_id', 'name', 'city']
        if sort_by not in allowed_sort_fields:
            sort_by = 'school_id'

        if direction == 'desc':
            sort_by = f'-{sort_by}'

        return queryset.order_by(sort_by)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_sort'] = self.request.GET.get('sort', 'school_id')
        context['current_direction'] = self.request.GET.get('direction', 'asc')
        return context

class SchoolCreateView(HtmxCreateView):
    model = School
    form_class = SchoolForm
    template_name_prefix = 'schools'
    list_url_name = 'core:school_list'

class SchoolUpdateView(HtmxUpdateView):
    model = School
    form_class = SchoolForm
    template_name_prefix = 'schools'
    list_url_name = 'core:school_list'

class SchoolDeleteView(HtmxDeleteView):
    model = School
    template_name = 'schools/confirm_delete.html'
    template_name_prefix = 'schools'
    list_url_name = 'core:school_list'

# =============================================================================
# --- КЛАССЫ (SCHOOL CLASS) ---
# =============================================================================

class SchoolClassListView(HtmxListView):
    model = School
    template_name_prefix = 'classes'
    extra_context = {
        'title': 'Классы',
        'add_url': 'core:class_add',
        'edit_url': 'core:class_edit',
        'delete_url': 'core:class_delete'
    }

    def get_queryset(self):
        schools_qs = get_accessible_schools(self.request.user)
        return schools_qs.prefetch_related(
            Prefetch('classes', queryset=SchoolClass.objects.order_by('name'))
        ).order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'items' in context:
            context['schools'] = context.pop('items')
        return context

class SchoolClassCreateView(HtmxCreateView):
    model = SchoolClass
    form_class = SchoolClassForm
    template_name_prefix = 'classes'
    list_url_name = 'core:class_list'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if school_id := self.request.GET.get('school'):
            kwargs['school'] = get_object_or_404(School, pk=school_id)
            kwargs['initial'] = {'school': school_id}
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['school_id_for_htmx'] = self.request.GET.get('school')
        return context

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно создан.'
        messages.success(self.request, success_message)
        
        if self.request.htmx:
            school = self.object.school
            school = School.objects.prefetch_related(
                Prefetch('classes', queryset=SchoolClass.objects.order_by('name'))
            ).get(pk=school.pk)
            # Передаем все URL'ы, которые ожидает _school_card.html
            context = { 
                'school': school, 
                'add_url': 'core:class_add',
                'edit_url': 'core:class_edit',
                'delete_url': 'core:class_delete',
                'user': self.request.user 
            }
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            html = render_to_string('classes/_school_card.html', context, request=self.request)
            return HttpResponse(html, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

class SchoolClassUpdateView(HtmxUpdateView):
    model = SchoolClass
    form_class = SchoolClassForm
    template_name_prefix = 'classes'
    list_url_name = 'core:class_list'
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.object:
            kwargs['school'] = self.object.school
        return kwargs

    # VVVVVV ВОТ ГЛАВНОЕ ИСПРАВЛЕНИЕ VVVVVV
    def get_context_data(self, **kwargs):
        """
        Добавляем ID школы в контекст, чтобы hx-target в
        form_modal.html не был пустым.
        """
        context = super().get_context_data(**kwargs)
        # self.object - это редактируемый класс (например, "5Б")
        # self.object.school.pk - это ID его школы (например, 1)
        context['school_id_for_htmx'] = self.object.school.pk 
        return context
    # ^^^^^^ КОНЕЦ ИСПРАВЛЕНИЯ ^^^^^^

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно обновлен.'
        messages.success(self.request, success_message)
        
        if self.request.htmx:
            school = self.object.school
            school = School.objects.prefetch_related(
                Prefetch('classes', queryset=SchoolClass.objects.order_by('name'))
            ).get(pk=school.pk)
            # Передаем все URL'ы, которые ожидает _school_card.html
            context = { 
                'school': school, 
                'add_url': 'core:class_add',
                'edit_url': 'core:class_edit',
                'delete_url': 'core:class_delete',
                'user': self.request.user 
            }
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            html = render_to_string('classes/_school_card.html', context, request=self.request)
            return HttpResponse(html, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

class SchoolClassDeleteView(HtmxDeleteView):
    model = SchoolClass
    template_name = 'classes/confirm_delete.html'
    template_name_prefix = 'classes'
    list_url_name = 'core:class_list'

    def post(self, request, *args, **kwargs):
        if self.request.htmx:
            self.object = self.get_object()
            school = self.object.school
            item_name = str(self.object)
            self.object.delete()
            success_message = f'"{item_name}" успешно удален.'
            messages.error(self.request, success_message)

            school = School.objects.prefetch_related(
                Prefetch('classes', queryset=SchoolClass.objects.order_by('name'))
            ).get(pk=school.pk)
            # Передаем все URL'ы, которые ожидает _school_card.html
            context = { 
                'school': school, 
                'add_url': 'core:class_add',
                'edit_url': 'core:class_edit',
                'delete_url': 'core:class_delete',
                'user': self.request.user 
            }
            trigger = {"close-delete-modal": True, "show-message": {"text": success_message, "type": "error"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            html = render_to_string('classes/_school_card.html', context, request=self.request)
            return HttpResponse(html, headers=headers)
        
        return super().post(request, *args, **kwargs)

# =============================================================================
# --- ПРЕДМЕТЫ (SUBJECT) ---
# =============================================================================
class SubjectListView(HtmxListView): 
    model = Subject 
    template_name_prefix = 'subjects'
    extra_context = {
        'title': 'Предметы',
        'add_url': 'core:subject_add',
        'edit_url': 'core:subject_edit',
        'delete_url': 'core:subject_delete'
    }

    def get_queryset(self):
        # Эта логика из твоего файла, она верная
        if self.request.user.is_staff or self.request.user.is_superuser:
             return Subject.objects.all().order_by('name')
        else:
             return Subject.objects.all().order_by('name') 

class SubjectCreateView(HtmxCreateView):
    model = Subject
    form_class = SubjectForm
    template_name_prefix = 'subjects'
    list_url_name = 'core:subject_list'

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно создан.'
        messages.success(self.request, success_message)

        if self.request.htmx:
            list_view = SubjectListView() 
            list_view.request = self.request
            context = {'items': list_view.get_queryset(), **list_view.extra_context}
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
            return HttpResponse(html, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

class SubjectUpdateView(HtmxUpdateView):
    model = Subject
    form_class = SubjectForm
    template_name_prefix = 'subjects'
    list_url_name = 'core:subject_list'

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно обновлен.'
        messages.success(self.request, success_message)

        if self.request.htmx:
            list_view = SubjectListView()
            list_view.request = self.request
            context = {'items': list_view.get_queryset(), **list_view.extra_context}
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
            return HttpResponse(html, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

class SubjectDeleteView(HtmxDeleteView):
    model = Subject
    template_name = 'subjects/confirm_delete.html' 
    template_name_prefix = 'subjects'
    list_url_name = 'core:subject_list'

    def post(self, request, *args, **kwargs):
        if self.request.htmx:
            self.object = self.get_object()
            item_name = str(self.object)
            model_name = self.object.__class__.__name__ 
            self.object.delete()
            success_message = f'"{item_name}" успешно удален.'
            messages.error(self.request, success_message)

            trigger = {"close-delete-modal": True, "show-message": {"text": success_message, "type": "error"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            
            list_view = SubjectListView()
            list_view.request = self.request
            context = {'items': list_view.get_queryset(), **list_view.extra_context}

            html = render_to_string(f'{self.template_name_prefix}/_table.html', context, request=self.request)
            return HttpResponse(html, headers=headers)

        return super().post(request, *args, **kwargs)

# =============================================================================
# --- GAT ТЕСТЫ (GAT TEST) ---
# =============================================================================

def gat_test_list_view(request):
    # 1. Базовый запрос с оптимизацией и подсчетом результатов (для бейджей)
    base_qs = GatTest.objects.select_related('school', 'school_class', 'quarter') \
                             .annotate(result_count=Count('results')) \
                             .order_by('-test_date', 'name')
    
    # 2. Фильтр по правам доступа (безопасность)
    if not request.user.is_superuser:
        accessible_schools = get_accessible_schools(request.user)
        base_qs = base_qs.filter(school__in=accessible_schools)
    
    # 3. Фильтрация по Четверти (Табы)
    selected_quarter_id = request.GET.get('quarter')
    if selected_quarter_id and selected_quarter_id != 'all':
        base_qs = base_qs.filter(quarter_id=selected_quarter_id)

    # 4. Фильтрация по Поиску (Живой поиск)
    search_query = request.GET.get('search', '')
    if search_query:
        base_qs = base_qs.filter(school__name__icontains=search_query)

    # 5. Получаем список четвертей для табов (текущий год)
    current_year = AcademicYear.objects.order_by('-start_date').first()
    quarters = Quarter.objects.filter(year=current_year).order_by('start_date') if current_year else []

    # 6. Группировка по школам
    grouped_tests = defaultdict(list)
    for test in base_qs:
        if test.school: 
            grouped_tests[test.school].append(test)
    
    # Сортируем группы по названию школы
    sorted_grouped_tests = dict(sorted(grouped_tests.items(), key=lambda item: item[0].name))
    
    context = {
        'grouped_tests': sorted_grouped_tests, 
        'title': 'GAT Тесты',
        'quarters': quarters,
        'selected_quarter_id': selected_quarter_id,
        'search_query': search_query, # Возвращаем строку поиска, чтобы она не исчезала из поля
    }

    # 7. Поддержка HTMX (возвращаем только часть таблицы при фильтрации)
    if request.htmx:
        return render(request, 'gat_tests/partials/_test_list_content.html', context)

    return render(request, 'gat_tests/list.html', context)

def get_form_kwargs_for_gat(request, instance=None):
    """Вспомогательная функция для получения kwargs для GatTestForm."""
    kwargs = {'instance': instance}
    school_id = request.POST.get('school') or request.GET.get('school')
    if not school_id and instance:
        school_id = instance.school_id

    if school_id:
        try:
            kwargs['school'] = School.objects.get(pk=school_id)
        except School.DoesNotExist:
            pass
    return kwargs

class GatTestCreateView(HtmxCreateView):
    model = GatTest
    form_class = GatTestForm
    template_name_prefix = 'gat_tests'
    list_url_name = 'core:gat_test_list'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Назначить GAT Тест'
        return context

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно создан.'
        
        subjects = form.cleaned_data.get('subjects')
        school_class = form.cleaned_data.get('school_class')

        if subjects and school_class:
            for subject in subjects:
                question_count_key = f'questions_{subject.id}'
                if question_count_key in self.request.POST:
                    number_of_questions = self.request.POST.get(question_count_key)
                    if number_of_questions:
                        QuestionCount.objects.update_or_create(
                            school_class=school_class,
                            subject=subject,
                            defaults={'number_of_questions': int(number_of_questions)}
                        )
        
        if self.request.htmx:
            base_qs = GatTest.objects.select_related('school', 'school_class', 'quarter').order_by('-test_date', 'name')
            if not self.request.user.is_superuser:
                accessible_schools = get_accessible_schools(self.request.user)
                base_qs = base_qs.filter(school__in=accessible_schools)
            
            grouped_tests = defaultdict(list)
            for test in base_qs:
                if test.school:
                    grouped_tests[test.school].append(test)
            
            sorted_grouped_tests = dict(sorted(grouped_tests.items(), key=lambda item: item[0].name))
            
            html = render_to_string(
                'gat_tests/partials/_test_list_content.html', 
                {'grouped_tests': sorted_grouped_tests, 'user': self.request.user},
                request=self.request
            )
            
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger), 'HX-Retarget': '#test-list-container', 'HX-Reswap': 'innerHTML'}
            
            return HttpResponse(html, headers=headers)

        messages.success(self.request, success_message)
        return redirect(reverse_lazy(self.list_url_name))

    def form_invalid(self, form):
        print("ОШИБКИ ВАЛИДАЦИИ ФОРМЫ:", form.errors) 
        if self.request.htmx:
            school_id = self.request.POST.get('school')
            if school_id:
                try:
                    school = School.objects.get(pk=school_id)
                    form.fields['school_class'].queryset = SchoolClass.objects.filter(school=school, parent__isnull=True).order_by('name')
                    # ✨ Исправлено: Предметы глобальные
                    form.fields['subjects'].queryset = Subject.objects.all().order_by('name')
                except School.DoesNotExist:
                    pass
            
            response = render(self.request, 'gat_tests/_form_content.html', self.get_context_data(form=form))
            response['HX-Retrigger'] = 'form-validation-error'
            return response
        
        return super().form_invalid(form)


class GatTestUpdateView(HtmxUpdateView):
    model = GatTest
    form_class = GatTestForm
    template_name_prefix = 'gat_tests'
    list_url_name = 'core:gat_test_list'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактировать GAT Тест'
        return context

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно обновлен.'

        subjects = form.cleaned_data.get('subjects')
        school_class = form.cleaned_data.get('school_class')

        if subjects and school_class:
            for subject in subjects:
                question_count_key = f'questions_{subject.id}'
                if question_count_key in self.request.POST:
                    number_of_questions = self.request.POST.get(question_count_key)
                    if number_of_questions:
                        QuestionCount.objects.update_or_create(
                            school_class=school_class,
                            subject=subject,
                            defaults={'number_of_questions': int(number_of_questions)}
                        )
        
        if self.request.htmx:
            base_qs = GatTest.objects.select_related('school', 'school_class', 'quarter').order_by('-test_date', 'name')
            if not self.request.user.is_superuser:
                accessible_schools = get_accessible_schools(self.request.user)
                base_qs = base_qs.filter(school__in=accessible_schools)
            
            grouped_tests = defaultdict(list)
            for test in base_qs:
                if test.school:
                    grouped_tests[test.school].append(test)
            
            sorted_grouped_tests = dict(sorted(grouped_tests.items(), key=lambda item: item[0].name))
            
            html = render_to_string(
                'gat_tests/partials/_test_list_content.html', 
                {'grouped_tests': sorted_grouped_tests, 'user': self.request.user},
                request=self.request
            )
            
            trigger = {"close-modal": True, "show-message": {"text": success_message, "type": "success"}}
            headers = {'HX-Trigger': json.dumps(trigger), 'HX-Retarget': '#test-list-container', 'HX-Reswap': 'innerHTML'}

            return HttpResponse(html, headers=headers)
        
        messages.success(self.request, success_message)
        return redirect(reverse_lazy(self.list_url_name))

    def form_invalid(self, form):
        if self.request.htmx:
            school_id = self.request.POST.get('school')
            if school_id:
                try:
                    school = School.objects.get(pk=school_id)
                    form.fields['school_class'].queryset = SchoolClass.objects.filter(school=school, parent__isnull=True).order_by('name')
                    # ✨ Исправлено: Предметы глобальные
                    form.fields['subjects'].queryset = Subject.objects.all().order_by('name')
                except School.DoesNotExist:
                    pass
            
            response = render(self.request, 'gat_tests/_form_content.html', self.get_context_data(form=form))
            response['HX-Retrigger'] = 'form-validation-error'
            return response

        return super().form_invalid(form)

class GatTestDeleteView(HtmxDeleteView):
    model = GatTest
    template_name = 'gat_tests/confirm_delete.html'
    template_name_prefix = 'gat_tests'
    list_url_name = 'core:gat_test_list'

    def post(self, request, *args, **kwargs):
        if self.request.htmx:
            self.object = self.get_object()
            school = self.object.school 
            item_name = str(self.object)
            self.object.delete()
            success_message = f'"{item_name}" успешно удален.'

            trigger = {"close-delete-modal": True, "show-message": {"text": success_message, "type": "error"}}
            headers = {'HX-Trigger': json.dumps(trigger)}
            
            base_qs = GatTest.objects.select_related('school', 'school_class', 'quarter').order_by('-test_date', 'name')
            if not self.request.user.is_superuser:
                accessible_schools = get_accessible_schools(self.request.user)
                base_qs = base_qs.filter(school__in=accessible_schools)
            
            grouped_tests = defaultdict(list)
            for test in base_qs:
                if test.school:
                    grouped_tests[test.school].append(test)
            
            sorted_grouped_tests = dict(sorted(grouped_tests.items(), key=lambda item: item[0].name))
            
            html = render_to_string(
                'gat_tests/partials/_test_list_content.html', 
                {'grouped_tests': sorted_grouped_tests, 'user': self.request.user},
                request=self.request
            )
            
            headers['HX-Retarget'] = '#test-list-container'
            headers['HX-Reswap'] = 'innerHTML'
            
            return HttpResponse(html, headers=headers)

        return super().post(request, *args, **kwargs)

@login_required
def gat_test_delete_results_view(request, pk):
    gat_test = get_object_or_404(GatTest, pk=pk)
    results = gat_test.results.all()
    count = results.count()
    
    if request.method == 'POST':
        results.delete()
        messages.success(request, f'Все {count} результатов для теста "{gat_test.name}" были успешно удалены.')
        return redirect('core:gat_test_list')
        
    context = {
        'item': gat_test, 
        'count': count, 
        'title': f'Удалить результаты для {gat_test.name}', 
        'cancel_url': 'core:gat_test_list'
    }
    return render(request, 'results/confirm_delete_batch.html', context)

# =============================================================================
# --- ЗАМЕТКИ УЧИТЕЛЯ (TEACHER NOTE) ---
# =============================================================================
class TeacherNoteCreateView(LoginRequiredMixin, CreateView):
    model = TeacherNote
    form_class = TeacherNoteForm
    template_name = 'students/partials/note_form.html'

    def form_valid(self, form):
        form.instance.author = self.request.user
        form.instance.student_id = self.kwargs.get('student_pk')
        form.save()
        messages.success(self.request, 'Заметка добавлена.')
        return redirect('core:student_progress', student_id=self.kwargs.get('student_pk'))

class TeacherNoteDeleteView(HtmxDeleteView):
    model = TeacherNote
    
    def get_success_url(self):
        return reverse_lazy('core:student_progress', kwargs={'student_id': self.object.student_id})

# =============================================================================
# --- КОЛИЧЕСТВО ВОПРОСОВ (QUESTION COUNT) ---
# =============================================================================
class QuestionCountListView(HtmxListView):
    model = School
    template_name_prefix = 'question_counts'
    extra_context = {
        'title': 'Количество вопросов',
        'management_url': 'core:management',
        'single_add_url': 'core:question_count_add',
        'bulk_add_url': 'core:question_count_bulk_add',
        'edit_url': 'core:question_count_edit',
        'delete_url': 'core:question_count_delete',
    }
    
    def get_queryset(self):
        return get_accessible_schools(self.request.user).order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schools_list = list(context.pop('items', []))
        school_ids = [s.id for s in schools_list]
        all_qcs = QuestionCount.objects.filter(
            school_class__school_id__in=school_ids
        ).select_related('subject', 'school_class').order_by('school_class__name', 'subject__name')
        
        qcs_by_school = defaultdict(list)
        for qc in all_qcs:
            qcs_by_school[qc.school_class.school_id].append(qc)
            
        for school in schools_list:
            school.all_question_counts = qcs_by_school[school.id]
            
        context['schools'] = schools_list
        return context

class QuestionCountCreateView(HtmxCreateView):
    model = QuestionCount
    form_class = QuestionCountForm
    template_name_prefix = 'question_counts'
    list_url_name = 'core:question_count_list'

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно создан.'
        messages.success(self.request, success_message)
        
        if self.request.htmx:
            school = self.object.school_class.school
            return _get_question_count_htmx_response(self.request, school, success_message)

        return redirect(reverse_lazy(self.list_url_name))

class QuestionCountUpdateView(HtmxUpdateView):
    model = QuestionCount
    form_class = QuestionCountForm
    template_name_prefix = 'question_counts'
    list_url_name = 'core:question_count_list'

    def form_valid(self, form):
        self.object = form.save()
        success_message = f'"{self.object}" успешно обновлен.'
        messages.success(self.request, success_message)
        
        if self.request.htmx:
            school = self.object.school_class.school
            return _get_question_count_htmx_response(self.request, school, success_message)

        return redirect(reverse_lazy(self.list_url_name))

class QuestionCountDeleteView(HtmxDeleteView):
    model = QuestionCount
    template_name_prefix = 'question_counts'
    list_url_name = 'core:question_count_list'

    def post(self, request, *args, **kwargs):
        if self.request.htmx:
            self.object = self.get_object()
            school = self.object.school_class.school
            item_name = str(self.object)
            self.object.delete()
            success_message = f'"{item_name}" успешно удален.'
            messages.error(self.request, success_message)
            
            return _get_question_count_htmx_response(
                self.request, 
                school, 
                success_message, 
                message_type='error', 
                is_delete=True
            )
        
        return super().post(request, *args, **kwargs)

class QuestionCountBulkCreateView(HtmxFormView):
    form_class = QuestionCountBulkSchoolForm
    template_name_prefix = 'question_counts'
    list_url_name = 'core:question_count_list'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = "Массовое добавление"
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        if self.request.method in ('GET', 'POST'):
            data = self.request.GET.copy()
            data.update(self.request.POST)
            kwargs['data'] = data
        return kwargs

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        
        if 'schools' in request.GET:
            template = 'question_counts/partials/_bulk_modal_step_3_fields.html'
            return render(request, template, {'form': form})

        if 'academic_year' in request.GET:
            template = 'question_counts/partials/_bulk_modal_step_2_schools.html'
            return render(request, template, {'form': form})
        
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.htmx:
            return [f'{self.template_name_prefix}/bulk_form_modal.html']
        return [f'{self.template_name_prefix}/form.html']

    def form_valid(self, form):
        schools = form.cleaned_data['schools']
        school_class = form.cleaned_data['school_class']
        subject = form.cleaned_data['subject']
        number = form.cleaned_data['number_of_questions']
        
        updated_count = 0
        created_count = 0

        for school in schools:
            target_class = SchoolClass.objects.get(school=school, name=school_class.name)
            
            _, created = QuestionCount.objects.update_or_create(
                school_class=target_class,
                subject=subject,
                defaults={'number_of_questions': number}
            )
            if created: created_count += 1
            else: updated_count += 1
        
        success_message = f"Операция завершена. Создано: {created_count}, обновлено: {updated_count}."
        messages.success(self.request, success_message)

        if self.request.htmx:
            trigger = {
                "close-modal": True, 
                "show-message": {"text": success_message, "type": "success"},
                "force-refresh": True 
            }
            headers = {'HX-Trigger': json.dumps(trigger)}
            return HttpResponse(status=204, headers=headers)

        return redirect(reverse_lazy(self.list_url_name))

@login_required
def gat_test_duplicate_view(request, pk):
    """
    Создает копию GAT-теста.
    Копирует школу, класс, предметы, но сбрасывает дату и номер теста.
    """
    if not request.user.is_superuser:
        messages.error(request, "У вас нет прав для дублирования тестов.")
        return redirect('core:gat_test_list')
        
    original_test = get_object_or_404(GatTest, pk=pk)
    
    # 1. Создаем копию объекта в памяти (pk=None создает новый объект при сохранении)
    original_subjects = list(original_test.subjects.all()) # Сохраняем предметы
    
    # Копируем объект
    original_test.pk = None
    original_test.id = None
    original_test._state.adding = True
    
    # Меняем данные для нового теста
    # Логика авто-инкремента: если был GAT-1, станет GAT-2 (примерно)
    next_num = original_test.test_number + 1 if original_test.test_number < 4 else 4
    original_test.test_number = next_num
    original_test.name = f"{original_test.name} (Копия)" # Можно заменить логикой замены "GAT-1" на "GAT-2"
    original_test.test_date = timezone.now().date() # Сбрасываем дату на сегодня
    
    # Сохраняем основной объект
    original_test.save()
    
    # Восстанавливаем связи ManyToMany (предметы)
    original_test.subjects.set(original_subjects)
    
    messages.success(request, f"Тест успешно скопирован! Теперь это '{original_test.name}'. Отредактируйте дату и детали.")
    
    # Если запрос пришел из модального окна или HTMX - можно вернуть сигнал на обновление
    # Но проще перенаправить на список, так как список должен обновиться
    return redirect('core:gat_test_list')

@login_required
def gat_test_duplicate_school_view(request, school_pk):
    """
    Массовое дублирование тестов для всей школы.
    """
    if not request.user.is_superuser:
        messages.error(request, "Нет прав.")
        return HttpResponse(status=403)

    school = get_object_or_404(School, pk=school_pk)

    # 1. GET запрос - показываем форму
    if request.method == 'GET':
        # Загружаем четверти для выпадающего списка
        current_year = AcademicYear.objects.order_by('-start_date').first()
        quarters = Quarter.objects.filter(year=current_year).order_by('start_date')
        
        # Пытаемся угадать текущую выбранную четверть из URL или берем первую
        current_quarter_id = request.GET.get('current_quarter')
        
        context = {
            'school': school,
            'quarters': quarters,
            'current_quarter_id': current_quarter_id
        }
        return render(request, 'gat_tests/duplicate_school_modal.html', context)

    # 2. POST запрос - выполняем клонирование
    if request.method == 'POST':
        source_quarter_id = request.POST.get('source_quarter')
        target_quarter_id = request.POST.get('target_quarter')
        new_date_str = request.POST.get('new_date')
        
        target_quarter = get_object_or_404(Quarter, pk=target_quarter_id)

        # Находим все тесты в исходной четверти для этой школы
        source_tests = GatTest.objects.filter(
            school=school,
            quarter_id=source_quarter_id
        )

        if not source_tests.exists():
            messages.warning(request, "В выбранной исходной четверти нет тестов для копирования.")
            # Закрываем модалку без обновления списка
            trigger = {"close-modal": True}
            return HttpResponse(status=204, headers={'HX-Trigger': json.dumps(trigger)})

        count = 0
        for original in source_tests:
            # Копируем связи ManyToMany
            original_subjects = list(original.subjects.all())
            
            # Создаем копию
            original.pk = None
            original.id = None
            original._state.adding = True
            
            original.quarter = target_quarter
            original.test_date = new_date_str
            
            # Логика инкремента номера (1 -> 2 -> 3 -> 4)
            original.test_number = original.test_number + 1 if original.test_number < 4 else 4
            
            # Обновляем имя (Убираем старые пометки и добавляем правильное имя, если нужно)
            # Например, если было "Тест (Копия)", можно просто оставить чистое имя или добавить новую логику
            # Для простоты пока оставим имя как есть или обновим номер в имени, если оно там есть
            
            original.save()
            original.subjects.set(original_subjects)
            count += 1

        success_message = f"Успешно склонировано {count} тестов в {target_quarter.name}."
        messages.success(request, success_message)

        # ✨ Возвращаем ОБНОВЛЕННЫЙ СПИСОК (как при фильтрации)
        # Нам нужно вызвать логику gat_test_list_view, чтобы вернуть HTML списка
        # Самый простой способ - редирект на список, но HTMX ждет HTML.
        # Поэтому мы вручную сформируем контекст списка:
        
        # Trigger для закрытия модалки и показа сообщения
        trigger = {
            "close-modal": True, 
            "show-message": {"text": success_message, "type": "success"},
            # Важно: Заставим список обновиться, перейдя на вкладку новой четверти
            "update-list-url": reverse('core:gat_test_list') + f"?quarter={target_quarter_id}" 
        }
        
        # Хак для HTMX: говорим клиенту сделать GET запрос на список с новой четвертью
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps(trigger)
        response['HX-Location'] = reverse('core:gat_test_list') + f"?quarter={target_quarter_id}"
        return response