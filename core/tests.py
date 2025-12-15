# D:\New_GAT\core\tests.py (ПОЛНЫЙ И ИСПРАВЛЕННЫЙ КОД)

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
import pandas as pd
import io
import datetime

from .models import (
    AcademicYear, Quarter, School, SchoolClass, Subject,
    GatTest, Student, StudentResult
)
# Импортируем правильную функцию из сервисов
from .services import process_student_results_upload

class ServicesTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        """
        Подготавливает начальные данные в базе один раз для всего набора тестов.
        Это эффективнее, чем создавать их заново для каждого теста.
        """
        cls.year = AcademicYear.objects.create(name="2025", start_date="2025-09-01", end_date="2026-05-31")
        cls.quarter = Quarter.objects.create(name="1 четверть", year=cls.year, start_date="2025-09-01", end_date="2025-10-31")
        cls.school = School.objects.create(school_id="SCH01", name="Тестовая Школа", address="Тестовый адрес")

        # Создаем "базовый" класс "10", к которому будет привязан тест
        cls.base_class = SchoolClass.objects.create(name="10", school=cls.school)

        # --- ✨ ИСПРАВЛЕНИЕ 1: Убрано поле 'school' ---
        # Модель Subject больше не привязана к школе
        cls.math = Subject.objects.create(name="Математика", abbreviation="МАТ")
        cls.phys = Subject.objects.create(name="Физика", abbreviation="ФИЗ")

        # Создаем GAT-тест, привязанный к параллели "10"
        cls.gat_test = GatTest.objects.create(
            name="GAT для 10-х классов",
            test_number=1,
            test_date=datetime.date.today(),
            quarter=cls.quarter,
            school=cls.school,      
            school_class=cls.base_class # Привязываем к параллели "10"
        )
        
        cls.gat_test.subjects.add(cls.math, cls.phys)

    def create_test_excel_file(self):
        """
        Создает в памяти тестовый Excel-файл с помощью pandas.
        """
        df = pd.DataFrame({
            'Code': ['S-1001', 'S-1002', 'S-1003'],
            'Surname': ['Иванов', 'Петров', 'Сидоров'],
            'Name': ['Иван', 'Петр', 'Сидор'],
            'Section': ['А', 'А', 'Б'], # Сервис создаст классы '10А' и '10Б'
            'МАТ_1': [1, 0, 1], # Сидоров: 1
            'МАТ_2': [0, 1, 1], # Сидоров: 1
            'ФИЗ_1': [1, 1, 0], # Сидоров: 0
        })

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')
        output.seek(0)

        return SimpleUploadedFile(
            "test_results.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_process_excel_creates_correct_classes(self):
        """
        Основной тест.
        Проверяет, что сервис правильно создает классы (10А, 10Б),
        привязывает к ним учеников и корректно сохраняет их результаты.
        """
        excel_file = self.create_test_excel_file()
        
        # Вызываем правильную функцию и получаем (success, report)
        success, report = process_student_results_upload(self.gat_test, excel_file)

        # Проверяем, что отчет вернул успех
        self.assertTrue(success)
        
        # Проверяем ключи из нового отчета
        self.assertEqual(report['total_unique_students'], 3)
        self.assertEqual(report['created_students'], 3)
        self.assertEqual(len(report['errors']), 0)

        # Проверяем, что сервис создал подклассы
        self.assertTrue(SchoolClass.objects.filter(name='10А', school=self.school, parent=self.base_class).exists())
        self.assertTrue(SchoolClass.objects.filter(name='10Б', school=self.school, parent=self.base_class).exists())
        self.assertEqual(SchoolClass.objects.count(), 3) # 10, 10А, 10Б

        # Проверяем конкретного студента
        sidorov = Student.objects.get(student_id='S-1003')
        self.assertEqual(sidorov.last_name_ru, "Сидоров")
        self.assertEqual(sidorov.school_class.name, '10Б')

        # Проверяем его результат
        sidorov_result = StudentResult.objects.get(student=sidorov)
        
        # --- ✨ ИСПРАВЛЕНИЕ 2: Ожидаем словарь, а не список ---
        # Сервис services.py теперь сохраняет ответы в виде словаря:
        # { 'номер_вопроса': True/False }
        expected_scores = {
            str(self.math.id): { '1': True, '2': True }, # МАТ_1 = 1, МАТ_2 = 1
            str(self.phys.id): { '1': False }           # ФИЗ_1 = 0
        }
        
        # Проверяем, что структура в БД соответствует ожидаемой
        self.assertEqual(sidorov_result.scores_by_subject, expected_scores)
        
        # Проверяем, что итоговый балл подсчитан верно (1 + 1 + 0 = 2)
        # Эта логика в services.py была правильной
        self.assertEqual(sidorov_result.total_score, 2)