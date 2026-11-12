"""
apps/core/navigation.py
-----------------------
Single source of truth for sidebar navigation per role.

Each entry is a dict:
    {
        "label": str,          # display text
        "icon": str,           # Bootstrap Icons class (e.g. "bi-house")
        "url_name": str,       # namespaced URL name (e.g. "students:list")
        "url_args": list,      # optional positional args for reverse()
        "url_kwargs": dict,    # optional keyword args for reverse()
        "children": list,      # optional sub-items (same structure, no nesting beyond 1 level)
        "feature": str,        # optional slug in core.features; hides the entry when off
    }

A ``feature`` slug is the only reason an entry ever disappears: a shortcut to a
module this school has not switched on leads to a page that cannot answer. The
Finance portal's own entries carry no slug — the officer keeps the door open
even when the school stops billing.

The context processor (context_processors.py) resolves url_name → href and
checks if the current request path starts with that URL to mark it active.
"""

NAVIGATION: dict[str, list[dict]] = {
    "admin": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {
            "label": "Staff Users",
            "icon": "bi-person-badge",
            "url_name": "accounts:staff_list",
        },
        {
            "label": "Academics",
            "icon": "bi-mortarboard",
            "url_name": "academics:year_list",
        },
        {
            "label": "Students",
            "icon": "bi-people",
            "url_name": "students:list",
            "children": [
                {
                    "label": "All Students",
                    "icon": "bi-person-lines-fill",
                    "url_name": "students:list",
                },
                {
                    "label": "Add Student",
                    "icon": "bi-person-plus",
                    "url_name": "students:add",
                },
                {
                    "label": "Bulk Import",
                    "icon": "bi-upload",
                    "url_name": "students:bulk_import",
                },
                {
                    "label": "Blank Admission Form",
                    "icon": "bi-printer",
                    "url_name": "students:blank_pdf",
                },
                {
                    "label": "Promote Class",
                    "icon": "bi-arrow-up-circle",
                    "url_name": "students:promote",
                },
                {
                    "label": "Promotion History",
                    "icon": "bi-clock-history",
                    "url_name": "students:promotion_history",
                },
            ],
        },
        {
            "label": "Teachers",
            "icon": "bi-easel",
            "url_name": "teachers:list",
            "children": [
                {
                    "label": "All Teachers",
                    "icon": "bi-people",
                    "url_name": "teachers:list",
                },
                {
                    "label": "Teacher Workload",
                    "icon": "bi-person-lines-fill",
                    "url_name": "teachers:workload",
                },
                {
                    "label": "Subject Distribution",
                    "icon": "bi-grid-3x3-gap",
                    "url_name": "teachers:subject_distribution",
                },
                {
                    "label": "Assignments",
                    "icon": "bi-list-check",
                    "url_name": "teachers:assignment_list",
                },
                {
                    "label": "Assignment Matrix",
                    "icon": "bi-table",
                    "url_name": "teachers:assignment_matrix",
                },
            ],
        },
        {
            "label": "Results",
            "icon": "bi-bar-chart",
            "url_name": "results:sheet_list",
            "children": [
                {
                    "label": "Class Broadsheet",
                    "icon": "bi-grid-3x3-gap",
                    "url_name": "results:broadsheet",
                },
                {
                    "label": "Result Sheets",
                    "icon": "bi-table",
                    "url_name": "results:sheet_list",
                },
                {
                    "label": "Grading Setup",
                    "icon": "bi-sliders",
                    "url_name": "results:grading_list",
                },
            ],
        },
        {"label": "Finance", "icon": "bi-cash-stack", "url_name": "finance:overview"},
        {
            "label": "Communications",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
            "children": [
                {
                    "label": "Announcements",
                    "icon": "bi-megaphone",
                    "url_name": "communications:list",
                },
                {
                    "label": "New Announcement",
                    "icon": "bi-plus-circle",
                    "url_name": "communications:announcement_create",
                },
                {
                    "label": "Message Inbox",
                    "icon": "bi-inbox",
                    "url_name": "communications:inbox",
                },
            ],
        },
        {
            "label": "Activity",
            "icon": "bi-activity",
            "url_name": "core_activity:feed",
            "children": [
                {
                    "label": "Live Feed",
                    "icon": "bi-broadcast",
                    "url_name": "core_activity:feed",
                },
                {
                    "label": "Daily Report",
                    "icon": "bi-calendar-day",
                    "url_name": "core_activity:daily_report",
                },
            ],
        },
        {
            "label": "Audit Log",
            "icon": "bi-clipboard-data",
            "url_name": "core:audit_log",
        },
        {
            "label": "School Settings",
            "icon": "bi-gear",
            "url_name": "core_school:settings",
        },
        {
            "label": "Website",
            "icon": "bi-globe",
            "url_name": "public_admin:gallery_list",
            "children": [
                {
                    "label": "Gallery",
                    "icon": "bi-images",
                    "url_name": "public_admin:gallery_list",
                },
                {
                    "label": "Enquiries",
                    "icon": "bi-envelope-open",
                    "url_name": "public_admin:enquiry_list",
                },
            ],
        },
    ],
    "director": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {
            "label": "School Analysis",
            "icon": "bi-graph-up-arrow",
            "url_name": "dashboards:director_home",
        },
        {
            "label": "Performance",
            "icon": "bi-bar-chart-line",
            "url_name": "dashboards:director_performance",
            "children": [
                {
                    "label": "Class Overview",
                    "icon": "bi-list-columns",
                    "url_name": "dashboards:director_performance",
                },
            ],
        },
        {
            "label": "Fee Analysis",
            "icon": "bi-cash-stack",
            "url_name": "dashboards:director_fees",
            "children": [
                {
                    "label": "Collection Overview",
                    "icon": "bi-pie-chart",
                    "url_name": "dashboards:director_fees",
                },
            ],
        },
        {
            "label": "Staff Directory",
            "icon": "bi-person-badge",
            "url_name": "dashboards:director_staff",
        },
        {
            "label": "Learning Engagement",
            "icon": "bi-activity",
            "url_name": "learning:director_engagement",
            "feature": "engagement",
        },
        {
            "label": "Points Overview",
            "icon": "bi-bar-chart",
            "url_name": "learning:director_points",
            "feature": "engagement",
        },
        {
            "label": "Live Classes",
            "icon": "bi-broadcast",
            "url_name": "learning:director_points",
            "feature": "live_classes",
        },
        {
            "label": "Books of Account",
            "icon": "bi-journal-text",
            "url_name": "dashboards:director_books",
            "children": [
                {
                    "label": "Summary",
                    "icon": "bi-file-earmark-bar-graph",
                    "url_name": "dashboards:director_books",
                },
                {
                    "label": "Ledger",
                    "icon": "bi-list-check",
                    "url_name": "dashboards:director_books_ledger",
                },
                {
                    "label": "Trial Balance",
                    "icon": "bi-balance-scale",
                    "url_name": "dashboards:director_books_trial_balance",
                },
            ],
        },
        {
            "label": "Communications",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
            "children": [
                {
                    "label": "Announcements",
                    "icon": "bi-megaphone",
                    "url_name": "communications:list",
                },
                {
                    "label": "New Announcement",
                    "icon": "bi-plus-circle",
                    "url_name": "communications:announcement_create",
                },
                {
                    "label": "Message Inbox",
                    "icon": "bi-inbox",
                    "url_name": "communications:inbox",
                },
            ],
        },
        {
            "label": "Academics",
            "icon": "bi-mortarboard",
            "url_name": "academics:year_list",
        },
        {"label": "Students", "icon": "bi-people", "url_name": "students:list"},
        {
            "label": "Teachers",
            "icon": "bi-person-badge-fill",
            "url_name": "teachers:list",
        },
    ],
    "teacher": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {"label": "My Classes", "icon": "bi-people", "url_name": "teachers:my_classes"},
        {
            "label": "My Classrooms",
            "icon": "bi-easel2",
            "url_name": "learning:teacher_classrooms",
            "feature": "learning",
        },
        {
            "label": "Class Feed",
            "icon": "bi-megaphone",
            "url_name": "learning:feed_home",
            "feature": "learning",
        },
        {
            "label": "Live Classes",
            "icon": "bi-broadcast",
            "url_name": "learning:teacher_classrooms",
            "feature": "live_classes",
        },
        {
            "label": "Class Progress",
            "icon": "bi-graph-up",
            "url_name": "learning:teacher_classrooms",
            "feature": "engagement",
        },
        {
            "label": "Class Points",
            "icon": "bi-award",
            "url_name": "learning:teacher_classrooms",
            "feature": "engagement",
        },
        {
            "label": "Homework",
            "icon": "bi-journal-bookmark",
            "url_name": "homework:teacher_list",
        },
        {
            "label": "Enter Marks",
            "icon": "bi-pencil-square",
            "url_name": "teachers:marks_list",
        },
        {
            "label": "Announcements",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
        },
    ],
    "student": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {
            "label": "My Learning",
            "icon": "bi-bookshelf",
            "url_name": "learning:classrooms",
            "feature": "learning",
        },
        {
            "label": "Class Feed",
            "icon": "bi-megaphone",
            "url_name": "learning:feed_home",
            "feature": "learning",
        },
        {
            "label": "My Progress",
            "icon": "bi-graph-up-arrow",
            "url_name": "learning:student_progress",
            "feature": "engagement",
        },
        {
            "label": "My Badges",
            "icon": "bi-award",
            "url_name": "learning:student_badges",
            "feature": "engagement",
        },
        {
            "label": "Class Calendar",
            "icon": "bi-calendar3",
            "url_name": "learning:classrooms",
            "feature": "live_classes",
        },
        {
            "label": "My Results",
            "icon": "bi-bar-chart",
            "url_name": "results:student_results",
        },
        {
            "label": "Fee Statement",
            "icon": "bi-receipt",
            "url_name": "finance:student_statement_self",
            "feature": "fees",
        },
        {
            "label": "Homework",
            "icon": "bi-journal-bookmark",
            "url_name": "homework:list",
        },
        {
            "label": "Marked work",
            "icon": "bi-chat-heart",
            "url_name": "homework:marked_work",
            "feature": "homework_grading",
        },
        {
            "label": "Messages",
            "icon": "bi-chat-square-text",
            "url_name": "communications:my_messages",
            "children": [
                {
                    "label": "My Messages",
                    "icon": "bi-envelope",
                    "url_name": "communications:my_messages",
                },
                {
                    "label": "New Message",
                    "icon": "bi-plus-circle",
                    "url_name": "communications:complaint_create",
                },
            ],
        },
        {
            "label": "Announcements",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
        },
        {"label": "Profile", "icon": "bi-person", "url_name": "accounts:profile"},
    ],
    "finance": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {
            "label": "Finance Overview",
            "icon": "bi-cash-stack",
            "url_name": "finance:overview",
        },
        {
            "label": "Invoices",
            "icon": "bi-file-earmark-text",
            "url_name": "finance:invoice_list",
        },
        {
            "label": "Payments",
            "icon": "bi-cash-coin",
            "url_name": "finance:payment_list",
        },
        {
            "label": "Take Payment",
            "icon": "bi-wallet2",
            "url_name": "finance:payment_record",
        },
        {
            "label": "Fee Structures",
            "icon": "bi-list-task",
            "url_name": "finance:fee_structure_list",
        },
        {
            "label": "Expenses",
            "icon": "bi-arrow-down-circle",
            "url_name": "finance:expense_list",
        },
        {
            "label": "Chart of Accounts",
            "icon": "bi-diagram-2",
            "url_name": "finance:account_list",
        },
        {"label": "Ledger", "icon": "bi-journal-text", "url_name": "finance:ledger"},
        {
            "label": "Reports",
            "icon": "bi-file-earmark-bar-graph",
            "url_name": "finance:report_summary",
            "children": [
                {
                    "label": "Income & Expense",
                    "icon": "bi-file-earmark-text",
                    "url_name": "finance:report_summary",
                },
                {
                    "label": "Outstanding Fees",
                    "icon": "bi-exclamation-circle",
                    "url_name": "finance:outstanding_fees",
                },
                {
                    "label": "School Summary",
                    "icon": "bi-building",
                    "url_name": "finance:school_summary",
                },
                {
                    "label": "Class Reports",
                    "icon": "bi-diagram-3",
                    "url_name": "finance:class_reports",
                },
                {
                    "label": "Trial Balance",
                    "icon": "bi-balance-scale",
                    "url_name": "finance:trial_balance",
                },
            ],
        },
        {
            "label": "Fee Reminders",
            "icon": "bi-megaphone",
            "url_name": "finance:send_reminders",
        },
        {"label": "Students", "icon": "bi-people", "url_name": "students:list"},
        {
            "label": "Communications",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
        },
    ],
    "parent": [
        {
            "label": "Dashboard",
            "icon": "bi-speedometer2",
            "url_name": "accounts:dashboard_redirect",
        },
        {"label": "My Children", "icon": "bi-people", "url_name": "accounts:children"},
        {
            "label": "My Learning",
            "icon": "bi-bookshelf",
            "url_name": "learning:classrooms",
            "feature": "learning",
        },
        {
            "label": "Class Feed",
            "icon": "bi-megaphone",
            "url_name": "learning:feed_home",
            "feature": "learning",
        },
        {
            "label": "My Progress",
            "icon": "bi-graph-up-arrow",
            "url_name": "learning:student_progress",
            "feature": "engagement",
        },
        {
            "label": "My Badges",
            "icon": "bi-award",
            "url_name": "learning:student_badges",
            "feature": "engagement",
        },
        {
            "label": "Class Calendar",
            "icon": "bi-calendar3",
            "url_name": "learning:classrooms",
            "feature": "live_classes",
        },
        {
            "label": "Results",
            "icon": "bi-bar-chart",
            "url_name": "results:student_results",
        },
        {
            "label": "Fee Statement",
            "icon": "bi-receipt",
            "url_name": "finance:student_statement_self",
            "feature": "fees",
        },
        {
            "label": "Homework",
            "icon": "bi-journal-bookmark",
            "url_name": "homework:list",
        },
        {
            "label": "Marked work",
            "icon": "bi-chat-heart",
            "url_name": "homework:marked_work",
            "feature": "homework_grading",
        },
        {
            "label": "Messages",
            "icon": "bi-chat-square-text",
            "url_name": "communications:my_messages",
            "children": [
                {
                    "label": "My Messages",
                    "icon": "bi-envelope",
                    "url_name": "communications:my_messages",
                },
                {
                    "label": "New Message",
                    "icon": "bi-plus-circle",
                    "url_name": "communications:complaint_create",
                },
            ],
        },
        {
            "label": "Announcements",
            "icon": "bi-megaphone",
            "url_name": "communications:list",
        },
        {"label": "Profile", "icon": "bi-person", "url_name": "accounts:profile"},
    ],
}
