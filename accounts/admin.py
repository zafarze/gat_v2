# D:\New-GAT\accounts\admin.py

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile

admin.site.unregister(User)

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Профиль пользователя'
    # Обновляем список полей в соответствии с новой моделью
    fields = ('role', 'school', 'schools', 'subjects', 'homeroom_class', 'student', 'photo')
    # Используем filter_horizontal для удобного выбора
    filter_horizontal = ('schools', 'subjects',)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'first_name', 'last_name', 'is_staff', 'get_user_role')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups', 'profile__role') # Добавили фильтр по роли

    @admin.display(description='Роль')
    def get_user_role(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.get_role_display()
        return 'Нет профиля'

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'school', 'homeroom_class')
    list_filter = ('role', 'school')