# D:\New_GAT\core\urls.py (Полная финальная версия после рефакторинга)

from django.urls import path
from .views import reports
# --- Импорты из 'accounts' ---
from accounts import views as account_views

# --- Импорты из приложения 'core' ---
# Импортируем модули с view-функциями
from core.views import (
    api,
    dashboard,
    deep_analysis,
    grading,
    monitoring,
    permissions,
    reports,
    statistics,
    student_dashboard,
    student_exams,
    students
)
# Импортируем классы и функции из crud.py
from core.views.crud import (
    AcademicYearListView, AcademicYearCreateView, AcademicYearUpdateView, AcademicYearDeleteView,
    QuarterListView, QuarterCreateView, QuarterUpdateView, QuarterDeleteView,
    SchoolListView, SchoolCreateView, SchoolUpdateView, SchoolDeleteView,
    SchoolClassListView, SchoolClassCreateView, SchoolClassUpdateView, SchoolClassDeleteView,
    SubjectListView, SubjectCreateView, SubjectUpdateView, SubjectDeleteView,
    GatTestCreateView, GatTestUpdateView, GatTestDeleteView, gat_test_list_view, gat_test_delete_results_view,
    TeacherNoteCreateView, TeacherNoteDeleteView,
    management_dashboard_view,
    QuestionCountListView, QuestionCountCreateView, QuestionCountUpdateView, QuestionCountDeleteView,
    QuestionCountBulkCreateView
    # Функции load_class_and_subjects_for_gat и load_fields_for_qc теперь в api.py
)

app_name = 'core'

urlpatterns = [
    # =============================================================================
    # --- АУТЕНТИФИКАЦИЯ И ГЛАВНЫЕ СТРАНИЦЫ ---
    # =============================================================================
    path('', account_views.user_login, name='home'),
    path('login/', account_views.user_login, name='login'),
    path('logout/', account_views.user_logout, name='logout'),
    path('dashboard/', dashboard.dashboard_view, name='dashboard'),

    # =============================================================================
    # --- ПРОФИЛЬ И УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---
    # =============================================================================
    path('dashboard/profile/', account_views.profile, name='profile'),
    path('dashboard/students/class/<int:class_id>/export-excel/', students.export_students_excel, name='student_export_excel'),
    path('dashboard/users/', account_views.UserListView.as_view(), name='user_list'),
    path('dashboard/users/add/', account_views.UserCreateView.as_view(), name='user_add'),
    path('dashboard/users/<int:pk>/edit/', account_views.UserUpdateView.as_view(), name='user_edit'),
    path('dashboard/users/<int:pk>/delete/', account_views.UserDeleteView.as_view(), name='user_delete'),
    path('dashboard/users/<int:pk>/toggle-active/', account_views.toggle_user_active, name='user_toggle_active'),
    path('dashboard/permissions/', permissions.manage_permissions_view, name='manage_permissions'),
    path('dashboard/students/school/<int:school_id>/export-excel/', students.export_school_students_excel, name='school_student_export_excel'),

    # =============================================================================
    # --- ПАНЕЛЬ УПРАВЛЕНИЯ (CRUD ОПЕРАЦИИ) ---
    # =============================================================================
    path('dashboard/management/', management_dashboard_view, name='management'),
    path('management/data-cleanup/', students.data_cleanup_view, name='data_cleanup'),

    # Учебные годы
    path('dashboard/years/', AcademicYearListView.as_view(), name='year_list'),
    path('dashboard/years/add/', AcademicYearCreateView.as_view(), name='year_add'),
    path('dashboard/years/<int:pk>/edit/', AcademicYearUpdateView.as_view(), name='year_edit'),
    path('dashboard/years/<int:pk>/delete/', AcademicYearDeleteView.as_view(), name='year_delete'),

    # Четверти
    path('dashboard/quarters/', QuarterListView.as_view(), name='quarter_list'),
    path('dashboard/quarters/add/', QuarterCreateView.as_view(), name='quarter_add'),
    path('dashboard/quarters/<int:pk>/edit/', QuarterUpdateView.as_view(), name='quarter_edit'),
    path('dashboard/quarters/<int:pk>/delete/', QuarterDeleteView.as_view(), name='quarter_delete'),

    # Школы
    path('dashboard/schools/', SchoolListView.as_view(), name='school_list'),
    path('dashboard/schools/add/', SchoolCreateView.as_view(), name='school_add'),
    path('dashboard/schools/<int:pk>/edit/', SchoolUpdateView.as_view(), name='school_edit'),
    path('dashboard/schools/<int:pk>/delete/', SchoolDeleteView.as_view(), name='school_delete'),

    # Классы
    path('dashboard/classes/', SchoolClassListView.as_view(), name='class_list'),
    path('dashboard/classes/add/', SchoolClassCreateView.as_view(), name='class_add'),
    path('dashboard/classes/<int:pk>/edit/', SchoolClassUpdateView.as_view(), name='class_edit'),
    path('dashboard/classes/<int:pk>/delete/', SchoolClassDeleteView.as_view(), name='class_delete'),

    # Предметы
    path('dashboard/subjects/', SubjectListView.as_view(), name='subject_list'),
    path('dashboard/subjects/add/', SubjectCreateView.as_view(), name='subject_add'),
    path('dashboard/subjects/<int:pk>/edit/', SubjectUpdateView.as_view(), name='subject_edit'),
    path('dashboard/subjects/<int:pk>/delete/', SubjectDeleteView.as_view(), name='subject_delete'),

    # GAT Тесты
    path('dashboard/gat-tests/', gat_test_list_view, name='gat_test_list'),
    path('dashboard/gat-tests/add/', GatTestCreateView.as_view(), name='gat_test_add'),
    path('dashboard/gat-tests/<int:pk>/edit/', GatTestUpdateView.as_view(), name='gat_test_edit'),
    path('dashboard/gat-tests/<int:pk>/delete/', GatTestDeleteView.as_view(), name='gat_test_delete'),
    path('dashboard/gat-tests/<int:pk>/delete-results/', gat_test_delete_results_view, name='gat_test_delete_results'),

    # Количество вопросов
    path('dashboard/question-counts/', QuestionCountListView.as_view(), name='question_count_list'),
    path('dashboard/question-counts/add/', QuestionCountCreateView.as_view(), name='question_count_add'),
    path('dashboard/question-counts/bulk-add/', QuestionCountBulkCreateView.as_view(), name='question_count_bulk_add'),
    path('dashboard/question-counts/<int:pk>/edit/', QuestionCountUpdateView.as_view(), name='question_count_edit'),
    path('dashboard/question-counts/<int:pk>/delete/', QuestionCountDeleteView.as_view(), name='question_count_delete'),

    # =============================================================================
    # --- УЧЕНИКИ ---
    # =============================================================================
    path('dashboard/students/', students.student_school_list_view, name='student_school_list'),
    path('dashboard/students/class/<int:class_id>/', students.student_list_view, name='student_list'),
    path('dashboard/students/add/', students.StudentCreateView.as_view(), name='student_add'),
    path('dashboard/students/<int:pk>/edit/', students.StudentUpdateView.as_view(), name='student_edit'),
    path('dashboard/students/delete-multiple/', students.student_delete_multiple_view, name='student_delete_multiple'),
    path('dashboard/students/<int:pk>/delete/', students.StudentDeleteView.as_view(), name='student_delete'),
    path('dashboard/students/upload/', students.student_upload_view, name='student_upload'),
    path('dashboard/students/<int:student_id>/progress/', students.student_progress_view, name='student_progress'),
    path('dashboard/students/<int:student_id>/create-account/', students.create_student_user_account, name='student_create_account'),
    path('dashboard/students/user/<int:user_id>/reset-password/', students.student_reset_password, name='student_reset_password'),
    path('dashboard/students/user/<int:user_id>/delete-account/', students.delete_student_user_account, name='student_delete_account'),
    path('dashboard/students/class/<int:class_id>/create-and-export-accounts/', students.class_create_export_accounts, name='class_create_export_accounts'),
    path('dashboard/student/<int:student_pk>/notes/add/', TeacherNoteCreateView.as_view(), name='note_add'),
    path('dashboard/notes/<int:pk>/delete/', TeacherNoteDeleteView.as_view(), name='note_delete'),
    path('dashboard/students/school/<int:school_id>/parallels/', students.student_parallel_list_view, name='student_parallel_list'),
    path('dashboard/students/parallel/<int:parent_id>/classes/', students.student_class_list_view, name='student_class_list'),
    path('dashboard/students/parallel/<int:parallel_id>/all/', students.student_list_combined_view, name='student_list_combined'),
    path('dashboard/students/parallel/<int:parallel_id>/create-and-export-accounts/', students.parallel_create_export_accounts, name='parallel_create_export_accounts'),

    # =============================================================================
    # --- ОТЧЕТЫ, АНАЛИТИКА И ЭКСПОРТ ---
    # =============================================================================
    path('dashboard/results/upload/', reports.upload_results_view, name='upload_results'),
    path('dashboard/results/gat/<int:test_number>/', reports.detailed_results_list_view, name='detailed_results_list'),
    path('dashboard/results/<int:pk>/', reports.student_result_detail_view, name='student_result_detail'),
    path('dashboard/results/<int:pk>/delete/', reports.student_result_delete_view, name='student_result_delete'),
    path('dashboard/monitoring/', monitoring.monitoring_view, name='monitoring'),
    path('dashboard/grading/', grading.grading_view, name='grading'),
    path('dashboard/statistics/', statistics.statistics_view, name='statistics'), # Указывает на statistics.py
    path('dashboard/analysis/', reports.analysis_view, name='analysis'),
    path('dashboard/deep-analysis/', deep_analysis.deep_analysis_view, name='deep_analysis'),
    path('dashboard/results/archive/', reports.archive_years_view, name='results_archive'),
    path('dashboard/results/archive/<int:year_id>/', reports.archive_quarters_view, name='archive_quarters'),
    path('dashboard/results/archive/quarter/<int:quarter_id>/', reports.archive_schools_view, name='archive_schools'),
    path('dashboard/results/archive/quarter/<int:quarter_id>/school/<int:school_id>/', reports.archive_classes_view, name='archive_classes'),
    path('dashboard/results/archive/quarter/<int:quarter_id>/class/<int:class_id>/', reports.class_results_dashboard_view, name='class_results_dashboard'),
    path('dashboard/results/compare/<int:test1_id>/vs/<int:test2_id>/', reports.compare_class_tests_view, name='compare_class_tests'),
    path('archive/quarter/<int:quarter_id>/parent/<int:parent_class_id>/combined_report/', reports.combined_class_report_view, name='combined_class_report'),
    path('dashboard/results/archive/quarter/<int:quarter_pk>/school/<int:school_pk>/class/<int:class_pk>/', reports.archive_subclasses_view, name='archive_subclasses'),
    path('dashboard/results/gat/<int:test_number>/export/excel/', reports.export_detailed_results_excel, name='export_detailed_results_excel'),
    path('dashboard/results/gat/<int:test_number>/export/pdf/', reports.export_detailed_results_pdf, name='export_detailed_results_pdf'),
    path('dashboard/monitoring/export/pdf/', monitoring.export_monitoring_pdf, name='export_monitoring_pdf'),
    path('dashboard/monitoring/export/excel/', monitoring.export_monitoring_excel, name='export_monitoring_excel'),
    path('dashboard/grading/export/excel/', grading.export_grading_excel, name='export_grading_excel'),
    path('dashboard/grading/export/pdf/', grading.export_grading_pdf, name='export_grading_pdf'),

    # =============================================================================
    # --- API (ДЛЯ HTMX И JAVASCRIPT) ---
    # =============================================================================
    path('api/header-search/', api.header_search_api, name='api_header_search'),
    path('api/load-quarters/', api.load_quarters, name='api_load_quarters'),
    path('api/load-schools/', api.api_load_schools, name='api_load_schools'),
    path('api/load-classes/', api.load_classes, name='api_load_classes'),
    path('api/load-classes/', api.load_classes, name='api_load_classes'),
    path('api/load-subjects/', api.load_subjects, name='api_load_subjects'),
    path('api/load-classes-as-chips/', api.api_load_classes_as_chips, name='api_load_classes_as_chips'),
    path('api/load-subjects-for-filters/', api.load_subjects_for_filters, name='api_load_subjects_for_filters'), # Указывает на api.py
    path('api/notifications/', api.get_notifications_api, name='api_get_notifications'),
    path('api/notifications/mark-as-read/', api.mark_notifications_as_read, name='api_mark_notifications_as_read'),
    path('api/permissions/toggle-school/', api.toggle_school_access_api, name='api_toggle_school_access'), # Указывает на api.py
    path('api/permissions/toggle-subject/', api.toggle_subject_access_api, name='api_toggle_subject_access'), # Указывает на api.py
    path('htmx/load-class-and-subjects/', api.load_class_and_subjects_for_gat, name='load_class_and_subjects_for_gat'), # Указывает на api.py
    path('htmx/load-fields-for-qc/', api.load_fields_for_qc, name='load_fields_for_qc'), # Указывает на api.py
    path('htmx/load-subjects-for-user-form/', api.api_load_subjects_for_user_form, name='api_load_subjects_for_user_form'),
    

    # =============================================================================
    # --- КАБИНЕТ УЧЕНИКА ---
    # =============================================================================
    path('student/dashboard/', student_dashboard.student_dashboard_view, name='student_dashboard'),
    path('student/exams/', student_exams.exam_list_view, name='exam_list'),
    path('student/exams/<int:result_id>/review/', student_exams.exam_review_view, name='exam_review'),

]