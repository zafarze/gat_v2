# D:\New_GAT\core\views\__init__.py (Полная и финальная версия)

# --- Импорты из api.py ---
from .api import (
    load_quarters,
    load_classes,
    load_subjects,
    api_load_classes_as_chips,
    load_subjects_for_filters,
    get_notifications_api,
    mark_notifications_as_read,
    header_search_api,
    toggle_school_access_api,
    toggle_subject_access_api,
    load_class_and_subjects_for_gat,
    load_fields_for_qc,
)

# --- Импорты из crud.py ---
from .crud import (
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

# --- Импорты из dashboard.py ---
from .dashboard import (
    dashboard_view,
)

# --- Импорты из deep_analysis.py ---
from .deep_analysis import (
    deep_analysis_view,
)

# --- Импорты из grading.py ---
from .grading import (
    grading_view,
    export_grading_pdf,
    export_grading_excel,
)

# --- Импорты из monitoring.py ---
from .monitoring import (
    monitoring_view,
    export_monitoring_pdf,
    export_monitoring_excel,
)

# --- Импорты из permissions.py ---
from .permissions import (
    manage_permissions_view,
    # Функции toggle_school_access_api и toggle_subject_access_api теперь в api.py
    # Вспомогательные функции (get_accessible_schools и т.д.) обычно импортируются напрямую там, где нужны
)

# --- Импорты из reports.py ---
from .reports import (
    upload_results_view,
    detailed_results_list_view,
    student_result_detail_view,
    student_result_delete_view,
    archive_years_view,
    archive_quarters_view,
    archive_schools_view,
    archive_classes_view,
    class_results_dashboard_view,
    compare_class_tests_view,
    analysis_view,
    # statistics_view теперь в statistics.py
    export_detailed_results_excel,
    export_detailed_results_pdf,
    archive_subclasses_view,
    combined_class_report_view
)

# --- Импорты из statistics.py ---
from .statistics import (
    statistics_view,
    # api_load_subjects_for_filters теперь в api.py
)

# --- Импорты из student_dashboard.py ---
from .student_dashboard import (
    student_dashboard_view,
)

# --- Импорты из student_exams.py ---
from .student_exams import (
    exam_list_view,
    exam_review_view,
)

# --- Импорты из students.py ---
from .students import (
    student_school_list_view,
    student_list_view,
    StudentCreateView,
    StudentUpdateView,
    student_delete_multiple_view,
    StudentDeleteView,
    student_upload_view,
    student_progress_view,
    create_student_user_account,
    student_reset_password,
    delete_student_user_account,
    class_create_export_accounts,
    student_parallel_list_view,
    student_class_list_view,
    student_list_combined_view,
    parallel_create_export_accounts,
    data_cleanup_view
)

from .ai_chat import ai_chat_page, ai_ask_api