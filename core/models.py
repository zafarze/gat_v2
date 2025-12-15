# D:\New_GAT\core\models.py (ФИНАЛЬНАЯ ПОЛНАЯ ВЕРСИЯ)

from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q, F, UniqueConstraint
from django.utils import timezone
# ✨ 1. ДОБАВЬТЕ ЭТОТ ИМПОРТ ✨
from django.core.cache import cache 

# =============================================================================
# --- БАЗОВЫЕ И ВСПОМОГАТЕЛЬНЫЕ МОДЕЛИ ---
# =============================================================================

class BaseModel(models.Model):
    """Абстрактная базовая модель с общими полями аудита."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True
        ordering = ['-created_at']

class AcademicYear(BaseModel):
    """Модель учебного года."""
    name = models.CharField(max_length=100, unique=True, verbose_name="Название")
    start_date = models.DateField(verbose_name="Дата начала")
    end_date = models.DateField(verbose_name="Дата окончания")

    class Meta:
        ordering = ['-start_date']
        verbose_name = "Учебный год"
        verbose_name_plural = "Учебные годы"

    def __str__(self):
        return self.name

    def clean(self):
        # ... (ваш метод clean без изменений) ...
        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise ValidationError("Дата начала должна быть раньше даты окончания.")
            
            overlapping = AcademicYear.objects.filter(
                start_date__lt=self.end_date, end_date__gt=self.start_date
            ).exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError("Даты этого учебного года пересекаются с существующим.")

    # --- ✨✨✨ Методы для сброса кеша (теперь они будут работать) ✨✨✨ ---
    
    def save(self, *args, **kwargs):
        """
        Переопределяем метод save.
        При любом сохранении (создании или обновлении) - очищаем кеш.
        """
        cache.delete('all_archive_years')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Переопределяем метод delete.
        При удалении - очищаем кеш.
        """
        cache.delete('all_archive_years')
        super().delete(*args, **kwargs)

class Quarter(BaseModel):
    """Модель учебной четверти, привязанная к учебному году."""
    name = models.CharField(max_length=100, verbose_name="Название")
    year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='quarters', verbose_name="Учебный год")
    start_date = models.DateField(verbose_name="Дата начала")
    end_date = models.DateField(verbose_name="Дата окончания")

    class Meta:
        ordering = ['-year__start_date', 'start_date']
        verbose_name = "Четверть"
        verbose_name_plural = "Четверти"
        constraints = [
            UniqueConstraint(fields=['name', 'year'], name='unique_quarter_name_per_year')
        ]

    def __str__(self):
        return f"{self.name} ({self.year.name})"

    def clean(self):
        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise ValidationError("Дата начала должна быть раньше даты окончания.")
            if self.year and not (self.year.start_date <= self.start_date and self.year.end_date >= self.end_date):
                raise ValidationError("Даты четверти должны находиться в пределах учебного года.")

# =============================================================================
# --- МОДЕЛИ, СВЯЗАННЫЕ СО ШКОЛОЙ ---
# =============================================================================

class School(BaseModel):
    """Модель школы."""
    school_id = models.CharField(max_length=20, unique=True, verbose_name="ID Школы")
    name = models.CharField(max_length=200, unique=True, verbose_name="Название")
    address = models.CharField(max_length=300, verbose_name="Адрес", blank=True)
    city = models.CharField(max_length=100, verbose_name="Город", blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Школа"
        verbose_name_plural = "Школы"

    def __str__(self):
        return self.name

class Subject(BaseModel):
    """
    Модель Предмета (Глобальная, не привязана к школе)
    """
    name = models.CharField(
        max_length=100, 
        unique=True,  # Название предмета уникально
        verbose_name="Название предмета"
    )
    abbreviation = models.CharField(
        max_length=15, 
        blank=True, 
        null=True, 
        verbose_name="Сокращение"
    )

    class Meta:
        verbose_name = "Предмет"
        verbose_name_plural = "Предметы"
        ordering = ['name']

    def __str__(self):
        return self.name

class SchoolClass(BaseModel):
    """
    Модель учебного класса. Может быть обычным классом (5А) или параллелью (5).
    """
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='classes', verbose_name="Школа")
    name = models.CharField(max_length=10, verbose_name="Название класса", help_text="Например: 5А или 10")
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subclasses', verbose_name="Параллель")
    homeroom_teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='homeroom_classes', verbose_name="Классный руководитель")

    class Meta:
        ordering = ['school__name', 'name']
        verbose_name = "Класс / Параллель"
        verbose_name_plural = "Классы и Параллели"
        constraints = [
            UniqueConstraint(fields=['school', 'name'], name='unique_class_name_per_school')
        ]

    def __str__(self):
        if self.parent:
            return f"Класс {self.name} (Параллель {self.parent.name}, {self.school.name})"
        return f"Параллель {self.name} ({self.school.name})"
    
    def clean(self):
        if self.parent and self.parent.parent is not None:
            raise ValidationError("Класс-параллель не может быть подклассом другой параллели.")

# =============================================================================
# --- МОДЕЛИ ТЕСТИРОВАНИЯ (GAT) ---
# =============================================================================

class GatTest(BaseModel):
    """Модель GAT-теста."""
    TEST_NUMBER_CHOICES = [(1, 'GAT-1'), (2, 'GAT-2'), (3, 'GAT-3'), (4, 'GAT-4')]
    DAY_CHOICES = [(1, 'День 1'), (2, 'День 2')]

    name = models.CharField(max_length=255, verbose_name="Название теста")
    
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='gat_tests', verbose_name="Школа", null=True, blank=True)
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='gat_tests', verbose_name="Класс", null=True, blank=True)
    
    test_number = models.PositiveIntegerField(choices=TEST_NUMBER_CHOICES, verbose_name="Номер GAT")
    test_date = models.DateField(verbose_name="Дата проведения")
    quarter = models.ForeignKey(Quarter, on_delete=models.SET_NULL, null=True, related_name='gat_tests', verbose_name="Четверть")
    
    subjects = models.ManyToManyField(Subject, verbose_name="Предметы в тесте")

    day = models.PositiveSmallIntegerField(
        choices=DAY_CHOICES, 
        default=1, 
        verbose_name="День GAT"
    )

    class Meta:
        ordering = ['-test_date', 'test_number', 'day']
        verbose_name = "GAT Тест"
        verbose_name_plural = "GAT Тесты"

    def __str__(self):
        return self.name

    def clean(self):
        if self.test_date and self.quarter:
            if not (self.quarter.start_date <= self.test_date <= self.quarter.end_date):
                raise ValidationError("Дата теста должна находиться в пределах выбранной четверти.")

    @property
    def schools(self):
        # Это свойство, вероятно, больше не нужно или должно быть переписано,
        # так как `school_classes` (M2M) было заменено на `school` (FK)
        # Если вы используете `school` (FK), то:
        if self.school:
            return School.objects.filter(pk=self.school.pk)
        return School.objects.none()


class Topic(BaseModel):
    """Темы вопросов по предметам."""
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='topics', verbose_name="Предмет")
    name = models.CharField(max_length=200, verbose_name="Название темы")

    class Meta:
        ordering = ['subject', 'name']
        verbose_name = "Тема"
        verbose_name_plural = "Темы"
        constraints = [UniqueConstraint(fields=['subject', 'name'], name='unique_topic_per_subject')]

    def __str__(self):
        return f"{self.name} ({self.subject.name})"

class Question(BaseModel):
    """Модель вопроса в GAT-тесте."""
    gat_test = models.ForeignKey(GatTest, on_delete=models.CASCADE, related_name='questions', verbose_name="Тест")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='questions', verbose_name="Предмет", null=True)
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Тема")
    question_number = models.PositiveIntegerField(verbose_name="Номер вопроса")
    text = models.TextField(verbose_name="Текст вопроса", blank=True)
    points = models.PositiveIntegerField(default=1, verbose_name="Баллы")

    class Meta:
        ordering = ['gat_test', 'subject', 'question_number']
        verbose_name = "Вопрос"
        verbose_name_plural = "Вопросы"
        constraints = [
            UniqueConstraint(fields=['gat_test', 'subject', 'question_number'], name='unique_question_number_per_test_subject')
        ]

    def __str__(self):
        # Упрощенное представление для избежания ошибок, если subject=None
        subject_abbr = self.subject.abbreviation if self.subject else 'N/A'
        return f"Вопрос №{self.question_number} ({subject_abbr}) по тесту '{self.gat_test.name}'"

class AnswerOption(BaseModel):
    """Вариант ответа на вопрос."""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options', verbose_name="Вопрос")
    text = models.CharField(max_length=500, verbose_name="Текст ответа")
    is_correct = models.BooleanField(default=False, verbose_name="Правильный ответ")

    class Meta:
        ordering = ['question', 'id']
        verbose_name = "Вариант ответа"
        verbose_name_plural = "Варианты ответа"

    def __str__(self):
        return f"{self.text[:30]}... {'(✓)' if self.is_correct else ''}"

# =============================================================================
# --- МОДЕЛИ УЧЕНИКОВ И ИХ РЕЗУЛЬТАТОВ ---
# =============================================================================

class Student(BaseModel):
    """Модель ученика."""
    STATUS_CHOICES = [('ACTIVE', 'Активен'), ('TRANSFERRED', 'Переведен'), ('GRADUATED', 'Выпустился')]
    
    student_id = models.CharField(max_length=50, unique=True, verbose_name="ID ученика")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name='students', verbose_name="Класс")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='ACTIVE', verbose_name="Статус")
    
    last_name_ru = models.CharField(max_length=100, verbose_name='Фамилия (рус.)')
    first_name_ru = models.CharField(max_length=100, verbose_name='Имя (рус.)')
    
    last_name_tj = models.CharField(max_length=100, verbose_name='Насаб (точ.)', blank=True)
    first_name_tj = models.CharField(max_length=100, verbose_name='Ном (точ.)', blank=True)
    last_name_en = models.CharField(max_length=100, verbose_name='Surname (eng.)', blank=True)
    first_name_en = models.CharField(max_length=100, verbose_name='Name (eng.)', blank=True)

    class Meta:
        ordering = ['school_class', 'last_name_ru', 'first_name_ru']
        verbose_name = "Ученик"
        verbose_name_plural = "Ученики"
        indexes = [models.Index(fields=['student_id'])]

    def __str__(self):
        return f"{self.last_name_ru} {self.first_name_ru} ({self.school_class.name})"
    
    @property
    def full_name_ru(self):
        return f"{self.last_name_ru} {self.first_name_ru}"

    @property
    def full_name_tj(self):
        return f"{self.last_name_tj} {self.first_name_tj}" if self.last_name_tj and self.first_name_tj else ""

    @property
    def full_name_en(self):
        return f"{self.first_name_en} {self.last_name_en}" if self.first_name_en and self.last_name_en else ""

class StudentResult(BaseModel):
    """Общий результат ученика по GAT-тесту."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='results', verbose_name="Ученик")
    gat_test = models.ForeignKey(GatTest, on_delete=models.CASCADE, related_name='results', verbose_name="GAT тест")
    total_score = models.PositiveIntegerField(default=0, db_index=True, verbose_name="Общий балл")
    scores_by_subject = models.JSONField(default=dict, blank=True, verbose_name="Баллы по предметам")

    class Meta:
        ordering = ['-gat_test__test_date', '-total_score']
        verbose_name = "Результат ученика"
        verbose_name_plural = "Результаты учеников"
        constraints = [UniqueConstraint(fields=['student', 'gat_test'], name='unique_result_per_student_test')]

    def __str__(self):
         return f"Результат {self.student.full_name_ru} по тесту {self.gat_test.name}"

class StudentAnswer(BaseModel):
    """Хранит конкретный ответ ученика на конкретный вопрос."""
    result = models.ForeignKey(StudentResult, on_delete=models.CASCADE, related_name='answers', verbose_name="Общий результат")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='student_answers', verbose_name="Вопрос")
    is_correct = models.BooleanField(default=False, verbose_name="Ответ верный")

    class Meta:
        ordering = ['result', 'question__question_number']
        verbose_name = "Ответ ученика"
        verbose_name_plural = "Ответы учеников"
        constraints = [UniqueConstraint(fields=['result', 'question'], name='unique_answer_per_result_question')]


# =============================================================================
# --- ПРОЧИЕ МОДЕЛИ (ВОССТАНОВЛЕНЫ) ---
# =============================================================================

class TeacherNote(BaseModel):
    """Заметки преподавателей об учениках."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='notes', verbose_name="Студент")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='authored_notes', verbose_name="Автор")
    note = models.TextField(verbose_name="Текст заметки")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Заметка преподавателя"
        verbose_name_plural = "Заметки преподавателей"
        indexes = [models.Index(fields=['student', 'created_at'])]

    def __str__(self):
        return f'Заметка для {self.student} от {self.author.username}'

class Notification(BaseModel):
    """Модель уведомлений для пользователей."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name="Пользователь")
    message = models.CharField(max_length=255, verbose_name="Сообщение")
    is_read = models.BooleanField(default=False, db_index=True, verbose_name="Прочитано")
    link = models.URLField(max_length=255, blank=True, null=True, verbose_name="Ссылка")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        indexes = [models.Index(fields=['user', 'is_read'])]

    def __str__(self):
        return f"Уведомление для {self.user.username}"

class University(BaseModel):
    """Модель университета."""
    name = models.CharField(max_length=255, verbose_name="Название")
    city = models.CharField(max_length=100, verbose_name="Город")
    description = models.TextField(blank=True, verbose_name="Описание")
    website = models.URLField(blank=True, verbose_name="Веб-сайт")

    class Meta:
        verbose_name = "Университет"
        verbose_name_plural = "Университеты"
        ordering = ['name']
        
    def __str__(self):
        return self.name

class Faculty(BaseModel):
    """Модель факультета в университете."""
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='faculties', verbose_name="Университет")
    name = models.CharField(max_length=255, verbose_name="Название")
    required_subjects = models.ManyToManyField('Subject', verbose_name="Требуемые предметы")

    class Meta:
        verbose_name = "Факультет"
        verbose_name_plural = "Факультеты"
        ordering = ['university', 'name']
        
    def __str__(f):
        return f"{self.name} ({self.university.name})"
    
# =============================================================================
# --- МОДЕЛЬ ДЛЯ КОЛИЧЕСТВА ВОПРОСОВ (НОВАЯ) ---
# =============================================================================

class QuestionCount(BaseModel):
    """
    Хранит количество вопросов для конкретного предмета в конкретном классе.
    """
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='question_counts', verbose_name="Класс")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='question_counts', verbose_name="Предмет")
    number_of_questions = models.PositiveIntegerField(default=10, verbose_name="Количество вопросов")

    class Meta:
        ordering = ['school_class__school__name', 'school_class__name', 'subject__name']
        verbose_name = "Количество вопросов"
        verbose_name_plural = "Количества вопросов"
        constraints = [
            UniqueConstraint(fields=['school_class', 'subject'], name='unique_question_count_per_class_subject')
        ]

    def __str__(self):
        # Добавим проверку на случай, если school_class или subject будут None
        class_name = self.school_class.name if self.school_class else 'N/A'
        subject_name = self.subject.name if self.subject else 'N/A'
        return f"{subject_name} в классе {class_name}"