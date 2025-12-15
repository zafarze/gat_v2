# D:\New_GAT\accounts\views.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse # <<<--- УБЕДИСЬ, ЧТО reverse ИМПОРТИРОВАН
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash, authenticate # Добавлен authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.db import transaction
from django.contrib.auth.models import User
from django.db.models import Q
from core.models import Notification # <<<--- Импорт Notification
from django.contrib.auth.decorators import user_passes_test # Для toggle_user_active

# Локальные импорты
from .forms import ( # Импортируем все формы
    CustomUserCreationForm,
    CustomUserEditForm,
    UserProfileForm,
    EmailChangeForm
)
from .models import UserProfile
from .permissions import UserManagementPermissionMixin

# =============================================================================
# --- Представления для обычных пользователей (вход, выход, свой профиль) ---
# =============================================================================

def user_login(request):
    """ Обрабатывает вход пользователя. """
    if request.user.is_authenticated:
        # Если пользователь уже вошел, перенаправляем
        if hasattr(request.user, 'profile') and request.user.profile.is_student:
            return redirect('core:student_dashboard') # Студента - в его дашборд
        return redirect('core:dashboard') # Остальных - в основной дашборд

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # Перенаправление после успешного входа
            if hasattr(user, 'profile') and user.profile.is_student:
                return redirect('core:student_dashboard')
            return redirect('core:dashboard')
        else:
            # Выводим общую ошибку, если форма невалидна
            messages.error(request, "Пожалуйста, проверьте правильность логина и пароля.")
    else:
        # Для GET-запроса создаем пустую форму
        form = AuthenticationForm()
    # Рендерим шаблон входа
    return render(request, 'accounts/login.html', {'form': form})

@login_required
def user_logout(request):
    """ Обрабатывает выход пользователя. """
    logout(request)
    messages.info(request, "Вы вышли из системы.")
    return redirect('core:login') # Перенаправляем на страницу входа

@login_required
def profile(request):
    """ Отображает и обрабатывает формы на странице профиля пользователя. """
    user = request.user
    # Получаем профиль, создаем если его нет (на всякий случай)
    profile_instance, _ = UserProfile.objects.get_or_create(user=user)

    # Инициализируем все три формы для GET-запроса
    user_edit_form = CustomUserEditForm(instance=user) # Для имени/фамилии
    profile_form = UserProfileForm(instance=profile_instance, user=user) # Для фото и др. полей профиля (если есть)
    password_form = PasswordChangeForm(user) # Для смены пароля
    email_form = EmailChangeForm(user=user, initial={'new_email': user.email}) # Для смены Email

    if request.method == 'POST':
        action = request.POST.get('action') # Определяем, какая форма отправлена
        old_email = user.email # Запоминаем старый email для уведомления

        # --- Обработка формы обновления профиля (имя, фамилия, фото) ---
        if action == 'update_profile':
            user_edit_form = CustomUserEditForm(request.POST, instance=user)
            profile_form = UserProfileForm(request.POST, request.FILES, instance=profile_instance, user=user)

            if user_edit_form.is_valid() and profile_form.is_valid():
                user_edit_form.save() # Сохраняем User (first_name, last_name)
                profile_form.save() # Сохраняем UserProfile (photo)
                messages.success(request, 'Данные профиля успешно обновлены.')
                # Используем имя URL 'profile' из accounts/urls.py
                return redirect('core:profile') # <<<--- ПРОВЕРЬ ЭТОТ РЕДИРЕКТ
            # Если формы невалидны, они будут переданы в context ниже с ошибками

        # --- Обработка формы смены пароля ---
        elif action == 'change_password':
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user) # Важно для сохранения сессии
                messages.success(request, 'Ваш пароль был успешно изменен.')
                return redirect('profile') # <<<--- ПРОВЕРЬ ЭТОТ РЕДИРЕКТ
            # Если форма невалидна, она будет передана в context ниже с ошибками

        # --- Обработка формы смены Email ---
        elif action == 'change_email':
            email_form = EmailChangeForm(request.POST, user=user)
            if email_form.is_valid():
                new_email = email_form.cleaned_data['new_email']
                try:
                    user.email = new_email
                    user.username = new_email # Обновляем и username, т.к. они связаны
                    user.save()
                    messages.success(request, f'Ваш Email успешно изменен на {new_email}.')

                    # --- Отправка уведомления суперадминам ---
                    superusers = User.objects.filter(is_superuser=True)
                    message_text = f"Пользователь '{user.get_full_name() or user.username}' изменил email с '{old_email}' на '{new_email}'."
                    # Используем reverse для получения URL админки
                    admin_user_url = reverse('admin:auth_user_change', args=[user.pk])
                    for admin_user in superusers:
                        Notification.objects.create(user=admin_user, message=message_text, link=admin_user_url)
                    # --- Конец отправки ---

                    return redirect('profile') # <<<--- ПРОВЕРЬ ЭТОТ РЕДИРЕКТ
                except Exception as e:
                    messages.error(request, f"Произошла ошибка при сохранении нового Email: {e}")
            # Если форма email_form невалидна, она будет передана в context ниже с ошибками

    # Передаем ВСЕ инициализированные или невалидные формы в контекст
    context = {
        'user_edit_form': user_edit_form,
        'profile_form': profile_form,
        'password_form': password_form,
        'email_form': email_form,
    }
    return render(request, 'accounts/profile.html', context)

# =============================================================================
# --- CRUD-представления для УПРАВЛЕНИЯ пользователями (Админка/Директор) ---
# =============================================================================

class UserListView(UserManagementPermissionMixin, ListView):
    """ Отображает список пользователей с фильтрацией по ролям и пагинацией. """
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20 # Количество пользователей на странице

    def get_queryset(self):
        """ Фильтрует пользователей в зависимости от роли текущего пользователя. """
        # Базовый queryset: все пользователи, кроме Студентов, с предзагрузкой профиля
        base_queryset = User.objects.exclude(profile__role=UserProfile.Role.STUDENT).select_related('profile')
        user = self.request.user

        # Если текущий пользователь - Директор (не суперпользователь)
        if not user.is_superuser and hasattr(user, 'profile') and user.profile.is_director:
            director_schools = user.profile.schools.all() # Школы, к которым у Директора есть доступ
            # Директор видит только Учителей и Кл. руководителей из своих школ
            base_queryset = base_queryset.filter(
                # Либо основная школа учителя в списке школ директора
                Q(profile__school__in=director_schools) |
                # Либо школа класса кл.руководителя в списке школ директора
                Q(profile__homeroom_class__school__in=director_schools),
                # И роль должна быть Учитель или Кл. руководитель
                profile__role__in=[UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]
            ).distinct() # distinct() нужен из-за Q-объектов

        # Применяем фильтр по роли из GET-параметра (табы)
        role_filter = self.request.GET.get('role', 'all')
        if role_filter == 'administrators':
            # Фильтр для вкладки "Администраторы" (is_staff=True)
            return base_queryset.filter(is_staff=True).order_by('last_name', 'first_name')
        elif role_filter != 'all':
             # Фильтр для конкретной роли (Директор, Эксперт и т.д.)
            # Для Директора этот фильтр сработает поверх уже примененного выше
            return base_queryset.filter(profile__role=role_filter).order_by('last_name', 'first_name')

        # Если 'all' или нет фильтра, возвращаем базовый queryset
        return base_queryset.order_by('last_name', 'first_name')

    def get_context_data(self, **kwargs):
        """ Добавляет заголовок, активную вкладку и список вкладок в контекст. """
        context = super().get_context_data(**kwargs)
        context['title'] = 'Управление пользователями'
        context['active_tab'] = self.request.GET.get('role', 'all')
        # Список вкладок для навигации
        context['role_tabs'] = [
            {'key': 'all', 'name': 'Все'},
            {'key': 'administrators', 'name': 'Администраторы'},
            {'key': UserProfile.Role.GENERAL_DIRECTOR, 'name': 'Ген. директоры'},
            {'key': UserProfile.Role.DIRECTOR, 'name': 'Директоры'},
            {'key': UserProfile.Role.EXPERT, 'name': 'Эксперты'},
            {'key': UserProfile.Role.TEACHER, 'name': 'Учителя'},
            {'key': UserProfile.Role.HOMEROOM_TEACHER, 'name': 'Кл. руководители'},
        ]
        # Убираем вкладки, которые Директор не должен видеть
        if not self.request.user.is_superuser and hasattr(self.request.user, 'profile') and self.request.user.profile.is_director:
             context['role_tabs'] = [tab for tab in context['role_tabs'] if tab['key'] in ['all', UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]]
             # Устанавливаем 'all' активной по умолчанию для Директора
             if context['active_tab'] not in ['all', UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]:
                 context['active_tab'] = 'all'

        return context


class UserCreateView(UserManagementPermissionMixin, CreateView):
    """ Создание нового пользователя (User) и его профиля (UserProfile). """
    template_name = 'accounts/user_form.html'
    form_class = CustomUserCreationForm # Форма для User
    success_url = reverse_lazy('core:user_list') # Используем 'core:user_list'

    def get_context_data(self, **kwargs):
        """ Добавляет форму профиля и заголовок в контекст. """
        context = super().get_context_data(**kwargs)
        context['title'] = 'Добавление пользователя'
        # Если profile_form еще не в контексте (например, при GET-запросе), создаем ее
        if 'profile_form' not in context:
            context['profile_form'] = UserProfileForm(user=self.request.user) # Передаем user для прав
        return context

    def post(self, request, *args, **kwargs):
        """ Обрабатывает POST-запрос, валидируя обе формы. """
        # form - это CustomUserCreationForm
        form = self.get_form()
        # profile_form - это UserProfileForm
        profile_form = UserProfileForm(request.POST, request.FILES, user=request.user) # Передаем user

        if form.is_valid() and profile_form.is_valid():
            # Если обе формы валидны, вызываем form_valid
            return self.form_valid(form, profile_form)
        else:
            # Если хотя бы одна невалидна, вызываем form_invalid
            return self.form_invalid(form, profile_form)

    def form_valid(self, form, profile_form):
        """ Сохраняет User и обновляет его созданный сигналом Profile. """
        try:
            with transaction.atomic(): # Используем транзакцию для атомарности
                # 1. Сохраняем User. Сигнал создает пустой UserProfile.
                user = form.save()

                # 2. Получаем profile_form с данными из POST и связываем с user.profile
                # Это ОБНОВИТ существующий пустой профиль данными из формы.
                profile_form_rebound = UserProfileForm(
                    self.request.POST,
                    self.request.FILES,
                    instance=user.profile, # <<<--- Связываем с существующим профилем
                    user=self.request.user   # <<<--- Передаем user для прав
                )

                # 3. Валидируем и сохраняем обновленный профиль
                if profile_form_rebound.is_valid():
                    profile_form_rebound.save()
                    messages.success(self.request, f"Пользователь {user.get_full_name() or user.username} успешно создан.")
                    return redirect(self.success_url)
                else:
                    # Если profile_form_rebound вдруг стал невалидным (маловероятно, но возможно)
                    # Откатываем транзакцию (создание user)
                    transaction.set_rollback(True)
                    messages.error(self.request, "Произошла ошибка в данных профиля при сохранении.")
                    # Возвращаем обе формы с ошибками
                    return self.form_invalid(form, profile_form_rebound)
        except Exception as e:
            # Ловим другие возможные ошибки при сохранении
            messages.error(self.request, f"Произошла непредвиденная ошибка: {e}")
            return self.form_invalid(form, profile_form) # Возвращаем формы с ошибками


    def form_invalid(self, form, profile_form):
        """ Возвращает шаблон с обеими формами и их ошибками. """
        context = self.get_context_data() # Получаем базовый контекст (title)
        context['form'] = form # Добавляем невалидную form (User)
        context['profile_form'] = profile_form # Добавляем невалидную profile_form
        return self.render_to_response(context)


class UserUpdateView(UserManagementPermissionMixin, UpdateView):
    """ Редактирование существующего User и его UserProfile. """
    model = User # Редактируем модель User
    template_name = 'accounts/user_form.html'
    form_class = CustomUserEditForm # Форма для редактирования User (email, имя, фамилия)
    success_url = reverse_lazy('core:user_list') # Используем 'core:user_list'

    def get_context_data(self, **kwargs):
        """ Добавляет форму профиля и заголовок в контекст. """
        context = super().get_context_data(**kwargs)
        # self.object здесь - это редактируемый User
        context['title'] = f'Редактирование: {self.object.get_full_name() or self.object.username}'
        # Если profile_form еще не в контексте (при GET-запросе), создаем ее
        if 'profile_form' not in context:
            # Инициализируем UserProfileForm с текущими данными профиля
            context['profile_form'] = UserProfileForm(instance=self.object.profile, user=self.request.user) # Передаем user для прав
        return context

    def post(self, request, *args, **kwargs):
        """ Обрабатывает POST-запрос, валидируя обе формы. """
        self.object = self.get_object() # Получаем редактируемого User
        # form - это CustomUserEditForm
        form = self.get_form()
        # profile_form - это UserProfileForm
        profile_form = UserProfileForm(
            request.POST,
            request.FILES,
            instance=self.object.profile, # Связываем с профилем редактируемого User
            user=request.user              # Передаем user для прав
        )

        if form.is_valid() and profile_form.is_valid():
            # Если обе валидны, вызываем form_valid
            return self.form_valid(form, profile_form)
        else:
            # Иначе вызываем form_invalid
            return self.form_invalid(form, profile_form)

    def form_valid(self, form, profile_form):
        """ Сохраняет изменения в User и UserProfile. """
        user = form.save() # Сохраняем изменения в User
        profile_form.save() # Сохраняем изменения в UserProfile
        messages.success(self.request, f"Данные пользователя {user.get_full_name() or user.username} успешно обновлены.")
        return redirect(self.success_url)

    def form_invalid(self, form, profile_form):
        """ Возвращает шаблон с обеими формами и их ошибками. """
        context = self.get_context_data() # Получаем базовый контекст (title, object)
        context['form'] = form # Добавляем невалидную form (User)
        context['profile_form'] = profile_form # Добавляем невалидную profile_form
        return self.render_to_response(context)


class UserDeleteView(UserManagementPermissionMixin, DeleteView):
    """ Удаление пользователя (User). Профиль удаляется каскадно. """
    model = User
    template_name = 'accounts/user_confirm_delete.html'
    success_url = reverse_lazy('core:user_list') # Используем 'core:user_list'

    def form_valid(self, form):
        """ Добавляет сообщение об удалении перед редиректом. """
        messages.error(self.request, f"Пользователь {self.object.get_full_name() or self.object.username} был удален.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """ Добавляет заголовок в контекст. """
        context = super().get_context_data(**kwargs)
        context['title'] = f'Удалить: {self.object.get_full_name() or self.object.username}'
        return context

    # Дополнительная защита от самоудаления
    def dispatch(self, request, *args, **kwargs):
        if self.get_object() == request.user:
            messages.error(request, "Вы не можете удалить свой собственный аккаунт.")
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)


# =============================================================================
# --- Вспомогательные функции (Активация/деактивация, Настройка прав) ---
# =============================================================================

def is_staff_check(user):
    """ Простая проверка для декоратора user_passes_test. """
    return user.is_staff or user.is_superuser

@user_passes_test(is_staff_check) # Доступно только админам
def toggle_user_active(request, pk):
    """ Активирует или деактивирует пользователя. """
    user_to_toggle = get_object_or_404(User, pk=pk)

    # Защита от изменения своего статуса или статуса суперпользователя
    if user_to_toggle == request.user:
        messages.error(request, "Вы не можете изменить свой собственный статус.")
    elif user_to_toggle.is_superuser:
        messages.warning(request, "Статус суперпользователя нельзя изменить.")
    else:
        # Меняем статус на противоположный
        user_to_toggle.is_active = not user_to_toggle.is_active
        user_to_toggle.save()
        status = "активирован" if user_to_toggle.is_active else "деактивирован"
        messages.info(request, f"Пользователь {user_to_toggle.get_full_name() or user_to_toggle.username} был {status}.")

    return redirect('core:user_list') # Используем 'core:user_list'

@user_passes_test(is_staff_check) # Доступно только админам
def manage_permissions(request):
    """ Отображает заглушку для страницы настройки прав. """
    # В будущем здесь будет логика для manage_permissions_view из core/views/permissions.py
    # Пока просто рендерим шаблон-заглушку
    return render(request, 'accounts/manage_permissions.html', {'title': 'Настройка прав доступа'})