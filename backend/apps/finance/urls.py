"""apps/finance/urls.py"""

from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    # Dashboard overview
    path("", views.FinanceOverviewView.as_view(), name="overview"),
    # Chart of Accounts
    path("accounts/", views.AccountListView.as_view(), name="account_list"),
    path(
        "accounts/add-expense/",
        views.AccountAddExpenseView.as_view(),
        name="account_add_expense",
    ),
    # Fee Structures
    path(
        "fee-structures/",
        views.FeeStructureListView.as_view(),
        name="fee_structure_list",
    ),
    path(
        "fee-structures/create/",
        views.FeeStructureCreateView.as_view(),
        name="fee_structure_create",
    ),
    path(
        "fee-structures/<int:pk>/",
        views.FeeStructureDetailView.as_view(),
        name="fee_structure_detail",
    ),
    path(
        "fee-structures/items/<int:pk>/students/",
        views.FeeItemStudentsView.as_view(),
        name="fee_item_students",
    ),
    path(
        "fee-structures/template/",
        views.FeeStructureTemplateDownloadView.as_view(),
        name="fee_structure_template",
    ),
    path(
        "fee-structures/upload/",
        views.FeeStructureUploadView.as_view(),
        name="fee_structure_upload",
    ),
    path(
        "fee-structures/upload/<int:pk>/preview/",
        views.FeeStructureUploadPreviewView.as_view(),
        name="fee_structure_upload_preview",
    ),
    path(
        "fee-structures/upload/<int:pk>/commit/",
        views.FeeStructureUploadCommitView.as_view(),
        name="fee_structure_upload_commit",
    ),
    # Invoices
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path(
        "invoices/generate/",
        views.InvoiceGenerateView.as_view(),
        name="invoice_generate_preview",
    ),
    path(
        "invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"
    ),
    # Payments
    path("payments/", views.PaymentListView.as_view(), name="payment_list"),
    path("payments/record/", views.PaymentRecordView.as_view(), name="payment_record"),
    path(
        "payments/<int:pk>/", views.PaymentDetailView.as_view(), name="payment_detail"
    ),
    path(
        "payments/<int:pk>/receipt/",
        views.PaymentReceiptPdfView.as_view(),
        name="payment_receipt_pdf",
    ),
    path(
        "payments/<int:pk>/reverse/",
        views.PaymentReverseView.as_view(),
        name="payment_reverse",
    ),
    # Adjustments
    path(
        "adjustments/create/",
        views.AdjustmentCreateView.as_view(),
        name="adjustment_create",
    ),
    # Ledger
    path("ledger/", views.LedgerView.as_view(), name="ledger"),
    # Reports
    path(
        "reports/trial-balance/", views.TrialBalanceView.as_view(), name="trial_balance"
    ),
    path("reports/summary/", views.ReportSummaryView.as_view(), name="report_summary"),
    path(
        "reports/classes/", views.ClassReportsView.as_view(), name="class_reports"
    ),
    path(
        "reports/outstanding/",
        views.OutstandingFeesView.as_view(),
        name="outstanding_fees",
    ),
    path(
        "reports/outstanding/export/",
        views.OutstandingFeesExportView.as_view(),
        name="outstanding_export",
    ),
    # Student Statement
    path(
        "students/<int:pk>/statement/",
        views.StudentStatementView.as_view(),
        name="student_statement",
    ),
    path(
        "students/<int:pk>/statement/pdf/",
        views.StudentStatementPdfView.as_view(),
        name="student_statement_pdf",
    ),
    path(
        "students/<int:pk>/notes/",
        views.StudentFinanceNotesView.as_view(),
        name="student_finance_notes",
    ),
    # Class / School Summaries
    path(
        "classes/<int:pk>/summary/",
        views.ClassSummaryView.as_view(),
        name="class_summary",
    ),
    path("school/summary/", views.SchoolSummaryView.as_view(), name="school_summary"),
    # Expenses
    path("expenses/", views.ExpenseListView.as_view(), name="expense_list"),
    path("expenses/record/", views.ExpenseRecordView.as_view(), name="expense_record"),
    # Fee Reminders
    path("reminders/", views.SendRemindersView.as_view(), name="send_reminders"),
    # Student/Parent self-service redirect
    path(
        "my-statement/",
        views.MyStatementRedirectView.as_view(),
        name="student_statement_self",
    ),
]
