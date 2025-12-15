# D:\New_GAT\accounts\forms.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from django import forms
from django.contrib.auth.models import User
from django.urls import reverse_lazy
from .models import UserProfile
from core.models import School, SchoolClass, Subject
from django.contrib.auth import authenticate

# Общие CSS классы для стилизации форм
input_class = 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm'
select_class = 'mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md'
select_multiple_class = f'{select_class}' # Убрана фиксированная высота, можно задать в шаблоне
checkbox_class = 'h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded'

class CustomUserCreationForm(forms.ModelForm):
    email = forms.EmailField(
        required=True,
        label="Email (будет использоваться для входа)",
        widget=forms.EmailInput(attrs={'class': input_class, 'autocomplete': 'email'})
    )
    first_name = forms.CharField(
        max_length=150, 
        required=True, 
        label="Имя", 
        widget=forms.TextInput(attrs={'class': input_class, 'autocomplete': 'given-name'})
    )
    last_name = forms.CharField(
        max_length=150, 
        required=True, 
        label="Фамилия", 
        widget=forms.TextInput(attrs={'class': input_class, 'autocomplete': 'family-name'})
    )
    password = forms.CharField(
        label="Пароль", 
        widget=forms.PasswordInput(attrs={'class': input_class, 'autocomplete': 'new-password'})
    )
    confirm_password = forms.CharField(
        label="Подтвердите пароль",
        widget=forms.PasswordInput(attrs={'class': input_class, 'autocomplete': 'new-password'})
    )

    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name')

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        
        if password and confirm_password and password != confirm_password:
            self.add_error('confirm_password', "Пароли не совпадают")
        
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.username = self.cleaned_data["email"] # Используем email как username для входа
        if commit:
            user.save()
        return user

class UserProfileForm(forms.ModelForm):
    """
    Умная форма профиля пользователя с динамическим отображением полей
    в зависимости от роли и прав текущего пользователя.
    """
    
    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # --- ПРИМЕНЕНИЕ ПРАВ ДОСТУПА ---
        if self.request_user and not self.request_user.is_superuser:
            if hasattr(self.request_user, 'profile') and self.request_user.profile.role == UserProfile.Role.DIRECTOR:
                director_schools = self.request_user.profile.schools.all()
                self.fields['role'].choices = [
                    ('', '---------'),
                    (UserProfile.Role.TEACHER, 'Учитель'),
                    (UserProfile.Role.HOMEROOM_TEACHER, 'Классный руководитель'),
                ]
                self.fields['school'].queryset = director_schools.order_by('name')
                
                # --- ✨ ИСПРАВЛЕНИЕ 1 (здесь) ---
                # Предметы больше не привязаны к школе, показываем все
                self.fields['subjects'].queryset = Subject.objects.all().order_by('name')
                
                self.fields['homeroom_class'].queryset = SchoolClass.objects.filter(school__in=director_schools, parent__isnull=False).order_by('name')
                self.fields['schools'].widget = forms.HiddenInput()
                self.fields['schools'].required = False
        
        # --- ДИНАМИЧЕСКАЯ ФИЛЬТРАЦИЯ ДЛЯ ЗАВИСИМЫХ ПОЛЕЙ ---
        school = None
        if 'school' in self.data:
            try:
                school_id = int(self.data.get('school'))
                school = School.objects.get(pk=school_id)
            except (ValueError, TypeError, School.DoesNotExist): pass
        elif self.instance and self.instance.school:
            school = self.instance.school
        
        # ✨ ИЗМЕНЕНИЕ 2: Специальная логика для роли "Эксперт" (Этот блок у тебя уже был, и он правильный)
        is_expert_role = self.data.get('role') == UserProfile.Role.EXPERT
        is_editing_expert = self.instance and self.instance.pk and self.instance.role == UserProfile.Role.EXPERT

        if is_expert_role or is_editing_expert:
            # ...тогда загружаем ВСЕ предметы из ВСЕХ школ
            self.fields['subjects'].queryset = Subject.objects.all().order_by('name')
            
        elif school:
            self.fields['homeroom_class'].queryset = SchoolClass.objects.filter(school=school, parent__isnull=False).order_by('name')
            
            # --- ✨ ИСПРАВЛЕНИЕ 3 (здесь) ---
            # Загружаем ВСЕ предметы, т.к. они больше не зависят от школы
            self.fields['subjects'].queryset = Subject.objects.all().order_by('name')
        
        elif not (self.instance and self.instance.pk):
             # --- ✨ ИСПРАВЛЕНИЕ 4 (здесь) ---
             # Показываем все предметы, даже если школа не выбрана (для Эксперта)
             self.fields['subjects'].queryset = Subject.objects.all().order_by('name')


    class Meta:
        model = UserProfile
        fields = ['role', 'photo', 'schools', 'school', 'subjects', 'homeroom_class', 'student']
        
        widgets = {
            'role': forms.Select(attrs={'class': select_class, 'id': 'id_role'}),
            'schools': forms.SelectMultiple(attrs={'class': f'{select_multiple_class} h-48', 'id': 'id_schools'}),
            'school': forms.Select(attrs={
                'class': select_class, 'id': 'id_school',
                # ВАЖНО: Этот HTMX-запрос теперь будет обновлять ТОЛЬКО поле 'subjects'
                # Нам нужно, чтобы он обновлял и 'homeroom_class'. 
                # Пока оставляем так, чтобы исправить ошибку, но в будущем это нужно улучшить.
                'hx-get': reverse_lazy('core:api_load_subjects_for_user_form'),
                'hx-target': '#subjects-field-container', # Цель - контейнер с предметами
                'hx-trigger': 'change',
            }),
            'subjects': forms.SelectMultiple(attrs={'class': f'{select_multiple_class} h-48', 'id': 'id_subjects'}),
            'homeroom_class': forms.Select(attrs={'class': select_class, 'id': 'id_homeroom_class'}),
            'student': forms.Select(attrs={'class': select_class, 'id': 'id_student'}),
        }
        
        # Этот блок у тебя уже был правильный
        labels = {
            'role': 'Роль пользователя',
            'schools': 'Доступ к школам (для Ген. директора / Директора)',
            'school': 'Основная школа (для Учителя/Классного руководителя)',
            'subjects': 'Предметы (для Учителя/Эксперта)',
            'homeroom_class': 'Классное руководство',
            'student': 'Привязка к ученику',
        }

    # Этот блок у тебя уже был правильный
    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        
        if role in [UserProfile.Role.GENERAL_DIRECTOR, UserProfile.Role.DIRECTOR]:
            if not cleaned_data.get('schools'):
                self.add_error('schools', 'Для этой роли необходимо выбрать хотя бы одну школу')
        
        elif role in [UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]:
            if not cleaned_data.get('school'):
                self.add_error('school', 'Для этой роли необходимо выбрать школу')
        
        if role in [UserProfile.Role.TEACHER, UserProfile.Role.EXPERT]:
             if not cleaned_data.get('subjects'):
                self.add_error('subjects', 'Для этой роли необходимо выбрать хотя бы один предмет')

        if role == UserProfile.Role.HOMEROOM_TEACHER:
            if not cleaned_data.get('homeroom_class'):
                self.add_error('homeroom_class', 'Классному руководителю необходимо назначить класс')
        
        elif role == UserProfile.Role.STUDENT:
            if not cleaned_data.get('student'):
                self.add_error('student', 'Для ученика необходимо выбрать карточку студента')
        
        return cleaned_data
    
    
class CustomUserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name')
        widgets = {
            'email': forms.EmailInput(attrs={'class': input_class}),
            'first_name': forms.TextInput(attrs={'class': input_class}),
            'last_name': forms.TextInput(attrs={'class': input_class}),
        }
        labels = {
            'email': 'Email',
            'first_name': 'Имя',
            'last_name': 'Фамилия'
        }

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Этот email уже используется другим пользователем")
        return email

class EmailChangeForm(forms.Form):
    """ Форма для смены Email пользователя. """
    new_email = forms.EmailField(
        label="Новый Email",
        required=True,
        widget=forms.EmailInput(attrs={'class': input_class, 'autocomplete': 'email'})
    )
    confirm_password = forms.CharField(
        label="Текущий пароль (для подтверждения)",
        required=True,
        widget=forms.PasswordInput(attrs={'class': input_class, 'autocomplete': 'current-password'})
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None) # Получаем текущего пользователя
        super().__init__(*args, **kwargs)

    def clean_new_email(self):
        """ Проверка, что новый email не занят другим пользователем. """
        new_email = self.cleaned_data.get('new_email')
        if new_email and self.user:
            # Ищем другого пользователя с таким email, исключая текущего
            if User.objects.filter(email__iexact=new_email).exclude(pk=self.user.pk).exists():
                raise forms.ValidationError("Этот email уже используется другим пользователем.")
        return new_email

    def clean_confirm_password(self):
        """ Проверка правильности текущего пароля. """
        confirm_password = self.cleaned_data.get('confirm_password')
        if self.user and not self.user.check_password(confirm_password):
            raise forms.ValidationError("Неверный текущий пароль.")
        # Альтернатива с authenticate (более строгая):
        # user_check = authenticate(username=self.user.username, password=confirm_password)
        # if user_check is None:
        #     raise forms.ValidationError("Неверный текущий пароль.")
        return confirm_password