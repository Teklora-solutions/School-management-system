"""
apps/dashboards/urls.py
"""

from django.urls import path

from . import views
from . import views_director as vd

app_name = "dashboards"

urlpatterns = [
    # Role home pages
    path("admin/", views.AdminHomeView.as_view(), name="admin_home"),
    path("director/", vd.DirectorHomeView.as_view(), name="director_home"),
    path("teacher/", views.TeacherHomeView.as_view(), name="teacher_home"),
    path("student/", views.StudentHomeView.as_view(), name="student_home"),
    path("finance/", views.FinanceHomeView.as_view(), name="finance_home"),
    path("parent/", views.ParentHomeView.as_view(), name="parent_home"),
    # ── Director sub-pages ────────────────────────────────────────────────
    # Class Performance
    path(
        "director/performance/",
        vd.ClassPerformanceView.as_view(),
        name="director_performance",
    ),
    path(
        "director/performance/<int:class_id>/",
        vd.ClassPerformanceDrilldownView.as_view(),
        name="director_performance_class",
    ),
    # Fee Analysis
    path(
        "director/fees/",
        vd.ClassFeeOverviewView.as_view(),
        name="director_fees",
    ),
    path(
        "director/fees/<int:class_id>/debtors/",
        vd.ClassFeeDebtorsView.as_view(),
        name="director_fee_debtors",
    ),
    # Staff Directory
    path(
        "director/staff/",
        vd.StaffDirectoryView.as_view(),
        name="director_staff",
    ),
    # Books of Account
    path(
        "director/books/",
        vd.BooksOverviewView.as_view(),
        name="director_books",
    ),
    path(
        "director/books/ledger/",
        vd.BooksLedgerView.as_view(),
        name="director_books_ledger",
    ),
    path(
        "director/books/trial-balance/",
        vd.BooksTrialBalanceView.as_view(),
        name="director_books_trial_balance",
    ),
    # Exports
    path(
        "director/export/performance/",
        vd.ExportClassPerformanceExcelView.as_view(),
        name="director_export_performance",
    ),
    path(
        "director/export/fees/",
        vd.ExportClassFeeExcelView.as_view(),
        name="director_export_fees",
    ),
    path(
        "director/export/staff/",
        vd.ExportStaffDirectoryExcelView.as_view(),
        name="director_export_staff",
    ),
    path(
        "director/export/books-pdf/",
        vd.ExportBooksReportPdfView.as_view(),
        name="director_export_books_pdf",
    ),
]
