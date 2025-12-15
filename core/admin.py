# D:\New_GAT\core\admin.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from django.contrib import admin
from django.db.models import Count

from .models import (
    AcademicYear, Quarter, School, SchoolClass, Subject,
    GatTest, Student, StudentResult, TeacherNote, QuestionCount
)

# ==========================================================
# --- УЛУЧШЕННЫЕ АДМИН-ПАНЕЛИ ---
# ==========================================================

@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    """Админка для Учебных Годов."""
    list_display = ('name', 'start_date', 'end_date')
    search_fields = ('name',)


@admin.register(Quarter)
class QuarterAdmin(admin.ModelAdmin):
    """Админка для Четвертей."""
    list_display = ('name', 'year', 'start_date', 'end_date')
    list_filter = ('year',)
    search_fields = ('name',)
    autocomplete_fields = ['year']


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    """Админка для Школ."""
    list_display = ('name', 'school_id', 'city', 'class_count')
    search_fields = ('name', 'city', 'school_id')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(
            _class_count=Count('classes', distinct=True)
        )
        return queryset

    def class_count(self, obj):
        return obj._class_count
    class_count.short_description = 'Кол-во классов'
    class_count.admin_order_field = '_class_count'


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    """Админка для Классов."""
    list_display = ('name', 'school', 'parent', 'student_count')
    list_filter = ('school',)
    search_fields = ('name', 'school__name')
    list_select_related = ('school', 'parent')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(
            _student_count=Count('students', distinct=True)
        )
        return queryset

    def student_count(self, obj):
        return obj._student_count
    student_count.short_description = 'Кол-во учеников'
    student_count.admin_order_field = '_student_count'


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    """
    Админка для Предметов (Исправленная).
    Убраны все ссылки на удаленное поле 'school'.
    """
    list_display = ('name', 'abbreviation')
    search_fields = ('name', 'abbreviation')
    # list_filter и autocomplete_fields удалены,
    # так как они ссылались на 'school'


@admin.register(GatTest)
class GatTestAdmin(admin.ModelAdmin):
    """
    Админка для GAT Тестов.
    Логика фильтров и отображения адаптирована
    под новую структуру модели с ForeignKey вместо ManyToMany.
    """
    list_display = ('name', 'school', 'school_class', 'test_date', 'quarter')
    list_filter = ('school', 'school_class', 'quarter', 'test_date')
    search_fields = ('name', 'school__name', 'school_class__name')
    autocomplete_fields = ['school', 'school_class', 'quarter']
    date_hierarchy = 'test_date'
    ordering = ('-test_date',)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    """Админка для Учеников."""
    # Используем 'full_name_ru' для отображения
    list_display = ('full_name_ru', 'student_id', 'school_class', 'status')
    list_filter = ('status', 'school_class__school',)
    search_fields = ('last_name_ru', 'first_name_ru', 'student_id')
    ordering = ('school_class', 'last_name_ru', 'first_name_ru')
    list_select_related = ('school_class', 'school_class__school')
    autocomplete_fields = ['school_class']


@admin.register(StudentResult)
class StudentResultAdmin(admin.ModelAdmin):
    """Админка для Результатов Учеников."""
    # ✨ ИЗМЕНЕНИЕ: Добавил 'total_score' для наглядности
    list_display = ('student', 'gat_test', 'display_scores', 'total_score')
    list_filter = ('gat_test__school', 'gat_test__quarter', 'gat_test')
    search_fields = ('student__last_name_ru', 'student__student_id', 'gat_test__name')
    # ✨ ИЗМЕНЕНИЕ: Добавил 'gat_test__school' для оптимизации
    list_select_related = ('student', 'gat_test', 'gat_test__school')
    autocomplete_fields = ['student', 'gat_test']

    @admin.display(description='Результаты (предметы)')
    def display_scores(self, obj):
        """
        ✨ ИСПРАВЛЕННЫЙ МЕТОД
        Этот метод читает JSON-поле 'scores_by_subject' и выводит
        названия предметов, по которым есть данные.
        """
        # 1. Обращаемся к правильному полю
        if not isinstance(obj.scores_by_subject, dict) or not obj.scores_by_subject:
            return "Нет данных"
        
        try:
            # 2. Создаем словарь { '1': 'Математика', '2': 'Физика' }
            #    Используем M2M-связь 'subjects' из модели GatTest
            subject_map = {
                str(s.id): s.name 
                for s in obj.gat_test.subjects.all()
            }
            
            # 3. Собираем названия
            subject_names = [
                # Используем .get() для безопасного получения имени
                subject_map.get(sub_id, f"ID {sub_id}?") 
                for sub_id in obj.scores_by_subject.keys()
            ]
            
            return ", ".join(subject_names)

        except Exception:
            # Если что-то пошло не так (например, у теста не заданы subjects),
            # просто вернем ключи (ID предметов)
            return ", ".join(obj.scores_by_subject.keys())


@admin.register(QuestionCount)
class QuestionCountAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'subject', 'number_of_questions')
    list_filter = ('school_class__school', 'subject')
    search_fields = ('school_class__name', 'subject__name')
    autocomplete_fields = ['school_class', 'subject']


@admin.register(TeacherNote)
class TeacherNoteAdmin(admin.ModelAdmin):
    """Админка для Заметок Учителей."""
    list_display = ('student', 'author', 'created_at', 'short_note')
    list_filter = ('author', 'student__school_class__school')
    search_fields = ('student__last_name_ru', 'author__username', 'note')
    autocomplete_fields = ['student', 'author']
    readonly_fields = ('created_at',)

    def short_note(self, obj):
        return obj.note[:50] + '...' if len(obj.note) > 50 else obj.note
    short_note.short_description = 'Заметка (коротко)'