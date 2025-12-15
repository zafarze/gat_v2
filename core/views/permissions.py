# D:\New_GAT\core\views\permissions.py (ПОЛНАЯ УЛУЧШЕННАЯ ВЕРСИЯ)

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import models # <<<--- ДОБАВЛЕНО: Импорт 'models' для Q-объектов
from accounts.models import UserProfile
# Убедись, что все нужные модели импортированы
from ..models import School, Subject, SchoolClass, Student, QuestionCount, GatTest

# =============================================================================
# --- VIEW ДЛЯ СТРАНИЦЫ УПРАВЛЕНИЯ ПРАВАМИ ---
# =============================================================================

@user_passes_test(lambda u: u.is_staff or u.is_superuser) # Ограничиваем доступ только админам
@login_required
def manage_permissions_view(request):
    """
    Отображает страницу управления правами доступа
    для Директоров (к школам) и Экспертов (к предметам).
    """
    # Загружаем Директоров и все школы для них (оптимизировано)
    directors = User.objects.filter(
        profile__role=UserProfile.Role.DIRECTOR
    ).select_related('profile').prefetch_related(
        'profile__schools' # Предзагружаем школы, к которым уже есть доступ
    ).order_by('last_name', 'first_name')

    all_schools = School.objects.all().order_by('name')

    # Загружаем Экспертов и все предметы для них (оптимизировано)
    experts = User.objects.filter(
        profile__role=UserProfile.Role.EXPERT,
        # is_staff=False, is_superuser=False # Можно убрать, если админ не может быть экспертом по логике
    ).select_related('profile').prefetch_related(
        'profile__subjects' # Предзагружаем предметы, к которым уже есть доступ
    ).order_by('last_name', 'first_name')

    # --- ✨ ИСПРАВЛЕНИЕ: Предметы больше не связаны со школой напрямую ---
    all_subjects = Subject.objects.all().order_by('name') # Просто получаем все предметы

    context = {
        'title': 'Управление правами доступа',
        'directors': directors,
        'all_schools': all_schools,
        'experts': experts,
        'all_subjects': all_subjects,
    }
    return render(request, 'permissions/manage.html', context) # Убедись, что путь к шаблону верный

# =============================================================================
# --- ОСНОВНЫЕ ФУНКЦИИ ДЛЯ ОПРЕДЕЛЕНИЯ ПРАВ ДОСТУПА ---
# =============================================================================

def get_accessible_schools(user):
    """
    Возвращает queryset школ, доступных пользователю в зависимости от его роли.
    (Логика здесь была в порядке)
    """
    # Проверяем аутентификацию и наличие профиля
    if not user.is_authenticated:
        return School.objects.none()
    profile = getattr(user, 'profile', None)
    if not profile:
        # Если профиля нет (маловероятно из-за сигналов, но для безопасности)
        # Суперпользователь все равно видит все
        return School.objects.all() if user.is_superuser else School.objects.none()

    # Суперпользователь, Ген. директор и Эксперт видят ВСЕ школы
    if user.is_superuser or profile.role in [UserProfile.Role.GENERAL_DIRECTOR, UserProfile.Role.EXPERT]:
        return School.objects.all()

    # Директор видит только школы, к которым у него есть прямой доступ через M2M
    if profile.role == UserProfile.Role.DIRECTOR:
        return profile.schools.all()

    # Учитель и Кл. руководитель видят только свою основную школу (ForeignKey 'school')
    if profile.role in [UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER] and profile.school:
        return School.objects.filter(pk=profile.school.pk)

    # Ученик видит только школу своего класса
    if profile.role == UserProfile.Role.STUDENT and profile.student and profile.student.school_class:
        # Добавлена проверка profile.student.school_class
        return School.objects.filter(pk=profile.student.school_class.school_id) # Оптимизация: используем school_id

    # Во всех остальных случаях возвращаем пустой queryset
    return School.objects.none()

def get_accessible_subjects(user):
    """
    Возвращает queryset предметов, доступных пользователю.
    (ИСПРАВЛЕНА ЛОГИКА ДЛЯ ДИРЕКТОРА И СТУДЕНТА)
    """
    if not user.is_authenticated:
        return Subject.objects.none()
    profile = getattr(user, 'profile', None)
    if not profile:
        return Subject.objects.all() if user.is_superuser else Subject.objects.none()

    # --- ✨ ИСПРАВЛЕНИЕ: Суперпользователь, Ген. директор, Директор видят ВСЕ предметы ---
    # Так как предметы глобальны, Директор тоже должен видеть все, чтобы назначать их учителям/экспертам
    if user.is_superuser or profile.role in [UserProfile.Role.GENERAL_DIRECTOR, UserProfile.Role.DIRECTOR]:
        return Subject.objects.all()

    # Эксперт, Учитель и Кл. руководитель видят только свои назначенные предметы (M2M 'subjects')
    if profile.role in [UserProfile.Role.EXPERT, UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]:
        return profile.subjects.all()

    # --- ✨ УПРОЩЕНИЕ: Студент видит все предметы ---
    # Логика показа предметов студенту сложна (зависит от тестов, QuestionCount).
    # Проще разрешить видеть все предметы, а фильтровать уже при показе РЕЗУЛЬТАТОВ.
    if profile.role == UserProfile.Role.STUDENT:
        return Subject.objects.all() # Студент видит все предметы

    # Остальные роли (если есть) не видят предметы
    return Subject.objects.none()

def get_accessible_classes(user):
    """
    Возвращает queryset классов, доступных пользователю.
    (Добавлен импорт models)
    """
    if not user.is_authenticated:
        return SchoolClass.objects.none()
    profile = getattr(user, 'profile', None)
    if not profile:
        # Суперюзер видит все классы, остальные - ничего
        return SchoolClass.objects.all() if user.is_superuser else SchoolClass.objects.none()

    # Суперпользователь, Ген. директор, Директор, Эксперт видят все классы в доступных им школах
    if user.is_superuser or profile.role in [UserProfile.Role.GENERAL_DIRECTOR, UserProfile.Role.DIRECTOR, UserProfile.Role.EXPERT]:
        # Получаем школы, доступные этим ролям
        schools = get_accessible_schools(user)
        # Возвращаем все классы из этих школ
        return SchoolClass.objects.filter(school__in=schools)

    # Кл. руководитель видит только свой класс и его параллель (если есть)
    if profile.role == UserProfile.Role.HOMEROOM_TEACHER and profile.homeroom_class:
        # Используем Q-объекты для объединения условий (класс ИЛИ его родитель)
        # Добавлена проверка на parent_id для оптимизации
        query = models.Q(pk=profile.homeroom_class.pk)
        if profile.homeroom_class.parent_id:
             query |= models.Q(pk=profile.homeroom_class.parent_id)
        return SchoolClass.objects.filter(query)

    # Учитель видит все классы в своей основной школе
    if profile.role == UserProfile.Role.TEACHER and profile.school:
        return SchoolClass.objects.filter(school=profile.school)

    # Ученик видит только свой класс
    if profile.role == UserProfile.Role.STUDENT and profile.student and profile.student.school_class:
        return SchoolClass.objects.filter(pk=profile.student.school_class.pk)

    return SchoolClass.objects.none()

def get_accessible_students(user):
    """
    Возвращает queryset учеников, доступных пользователю.
    (УПРОЩЕНА ЛОГИКА ДЛЯ УЧИТЕЛЯ)
    """
    if not user.is_authenticated:
        return Student.objects.none()
    profile = getattr(user, 'profile', None)
    if not profile:
        return Student.objects.all() if user.is_superuser else Student.objects.none()

    # Суперпользователь, Ген. директор, Директор, Эксперт видят всех учеников в доступных им классах
    if user.is_superuser or profile.role in [UserProfile.Role.GENERAL_DIRECTOR, UserProfile.Role.DIRECTOR, UserProfile.Role.EXPERT]:
        # Получаем классы, доступные этим ролям
        classes = get_accessible_classes(user)
        # Возвращаем всех студентов из этих классов
        return Student.objects.filter(school_class__in=classes)

    # Кл. руководитель видит всех учеников своего класса
    if profile.role == UserProfile.Role.HOMEROOM_TEACHER and profile.homeroom_class:
        return Student.objects.filter(school_class=profile.homeroom_class)

    # --- ✨ УПРОЩЕНИЕ: Учитель видит ВСЕХ учеников своей школы ---
    # Логика с QuestionCount была сложной. Проще дать доступ ко всем в школе.
    if profile.role == UserProfile.Role.TEACHER and profile.school:
        return Student.objects.filter(school_class__school=profile.school)

    # Ученик видит только себя
    if profile.role == UserProfile.Role.STUDENT and profile.student:
        return Student.objects.filter(pk=profile.student.pk)

    return Student.objects.none()