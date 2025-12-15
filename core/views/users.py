from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import DeleteView
from django.contrib.auth.models import User
from django.db import transaction

# Импортируем все необходимые формы
from accounts.forms import (
    CustomUserCreationForm, UserProfileForm, CustomUserEditForm
)
from accounts.models import UserProfile

@staff_member_required
def user_list_view(request):
    """
    Список всех пользователей для администраторов.
    Позволяет фильтровать пользователей по ролям.
    """
    queryset = User.objects.all().select_related('profile').order_by('last_name', 'first_name')
    active_tab = request.GET.get('role', 'all')
    
    # Создаем словарь для фильтров
    role_filters = {
        'directors': {'profile__role': 'DIRECTOR'},
        'experts': {'profile__role': 'EXPERT'},
        'teachers': {'profile__role__in': ['TEACHER', 'HOMEROOM_TEACHER']},
        'students': {'profile__role': 'STUDENT'},
        'admins': {'is_staff': True},
    }
    
    # Применяем фильтр из словаря, если он существует
    if active_tab in role_filters:
        queryset = queryset.filter(**role_filters[active_tab])

    context = {'title': 'Управление пользователями', 'users': queryset, 'active_tab': active_tab}
    return render(request, 'accounts/user_list.html', context)


@staff_member_required
@transaction.atomic 
def user_create_view(request):
    """
    Создание нового пользователя и ОБНОВЛЕНИЕ его профиля, созданного сигналом.
    """
    if request.method == 'POST':
        user_form = CustomUserCreationForm(request.POST)
        profile_form = UserProfileForm(request.POST, request.FILES)
        
        if user_form.is_valid() and profile_form.is_valid():
            # 1. Сохраняем пользователя. В этот момент сигнал автоматически
            #    создает для него пустой UserProfile.
            user = user_form.save()
            
            # 2. Теперь мы НЕ создаем новый профиль, а ОБНОВЛЯЕМ существующий.
            profile_form = UserProfileForm(request.POST, request.FILES, instance=user.profile)
            
            # 3. Сохраняем форму профиля, которая теперь выполнит UPDATE, а не INSERT.
            profile_form.save()

            messages.success(request, f'Пользователь {user.username} успешно создан.')
            return redirect('user_list')
    else:
        user_form = CustomUserCreationForm()
        profile_form = UserProfileForm()

    context = {
        'title': 'Добавить нового пользователя', 
        'user_form': user_form, 
        'profile_form': profile_form
    }
    return render(request, 'accounts/user_form.html', context)


@staff_member_required
@transaction.atomic
def user_update_view(request, pk):
    """
    Редактирование пользователя и его профиля администратором.
    """
    user = get_object_or_404(User, pk=pk)
    # Гарантируем наличие профиля
    profile, _ = UserProfile.objects.get_or_create(user=user)

    if request.method == 'POST':
        user_form = CustomUserEditForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, f'Данные пользователя {user.username} успешно обновлены.')
            return redirect('user_list')
    else:
        user_form = CustomUserEditForm(instance=user)
        profile_form = UserProfileForm(instance=profile)

    context = {
        'title': f'Редактировать пользователя: {user.username}', 
        'user_form': user_form, 
        'profile_form': profile_form
    }
    return render(request, 'accounts/user_form.html', context)


class UserDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    """
    Классовое представление для удаления пользователя.
    Доступно только персоналу.
    """
    model = User
    template_name = 'accounts/user_confirm_delete.html'
    success_url = reverse_lazy('core:user_list')
    
    def test_func(self):
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f"Удалить пользователя: {self.object.get_full_name() or self.object.username}"
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Пользователь {self.object.username} был успешно удален.")
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if self.get_object() == request.user:
            messages.error(request, "Вы не можете удалить свой собственный аккаунт.")
            return redirect('user_list')
        return super().dispatch(request, *args, **kwargs)


@staff_member_required
def user_toggle_active_view(request, pk):
    """
    Активация/деактивация пользователя.
    """
    user_to_toggle = get_object_or_404(User, pk=pk)
    
    if user_to_toggle == request.user:
        messages.error(request, "Вы не можете деактивировать свой собственный аккаунт.")
    elif user_to_toggle.is_superuser:
        messages.error(request, "Нельзя изменить статус суперпользователя.")
    else:
        user_to_toggle.is_active = not user_to_toggle.is_active
        user_to_toggle.save()
        status = "активирован" if user_to_toggle.is_active else "деактивирован"
        messages.success(request, f"Пользователь {user_to_toggle.username} был успешно {status}.")
        
    return redirect('user_list')