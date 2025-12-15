# D:\New_GAT\accounts\models.py (Полная версия с ролью SUPERUSER)

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import School, SchoolClass, Subject, Student

class UserProfile(models.Model):
    class Role(models.TextChoices):
        # ИЗМЕНЕНИЕ: Добавили роль SUPERUSER обратно в список
        SUPERUSER = 'SUPERUSER', 'Супер-администратор'
        GENERAL_DIRECTOR = 'GENERAL_DIRECTOR', 'Генеральный директор'
        DIRECTOR = 'DIRECTOR', 'Директор'
        EXPERT = 'EXPERT', 'Эксперт'
        TEACHER = 'TEACHER', 'Учитель'
        HOMEROOM_TEACHER = 'HOMEROOM_TEACHER', 'Классный руководитель'
        STUDENT = 'STUDENT', 'Ученик'

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True, related_name='profile')
    
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TEACHER, verbose_name="Роль")
    photo = models.ImageField(upload_to='profile_photos/', null=True, blank=True, verbose_name="Фотография")
    
    # Поля для привязок
    schools = models.ManyToManyField(
        School, blank=True, verbose_name="Доступ к школам (для Директора)",
        related_name='director_profiles'
    )
    school = models.ForeignKey(
        School, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Школа (для Учителя)", related_name='staff_profiles'
    )
    subjects = models.ManyToManyField(Subject, blank=True, verbose_name="Предметы (для Учителя/Эксперта)")
    homeroom_class = models.ForeignKey(
        SchoolClass, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Классное руководство", related_name='homeroom_teacher_profile'
    )
    
    student = models.OneToOneField(
        Student, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='user_profile'
    )

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

    # --- Свойства для быстрой проверки ролей ---
    
    # ИЗМЕНЕНИЕ: Добавили свойство для проверки новой роли.
    # Названо is_superuser_role, чтобы не конфликтовать со встроенным user.is_superuser
    @property
    def is_superuser_role(self):
        return self.role == self.Role.SUPERUSER

    @property
    def is_general_director(self):
        return self.role == self.Role.GENERAL_DIRECTOR

    @property
    def is_director(self):
        return self.role == self.Role.DIRECTOR

    @property
    def is_expert(self):
        return self.role == self.Role.EXPERT

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    @property
    def is_homeroom_teacher(self):
        return self.role == self.Role.HOMEROOM_TEACHER

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT


# Сигнал для автоматического создания профиля пользователя
@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    # Убедимся, что профиль сохраняется при каждом сохранении пользователя
    instance.profile.save()