# Backend — Django

The Django 5.1 project behind the School Management System: settings, 12 installed apps, the
double-entry finance engine, the results governance state machine, tests and operations scripts.

- Project settings/URLs: `backend/config/`
- Applications: `backend/apps/`
- Templates and static assets live **outside** this package, in [`../frontend/`](../frontend/README.md)
- Full handbook: [`../docs/SYSTEM_GUIDE.md`](../docs/SYSTEM_GUIDE.md) · [root README](../README.md)

---

## 1. Quick start

```bash
# from the repository root — create and populate the environment
python -m venv .venv && source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements/dev.txt
cp .env.example .env                                      # then set a real SECRET_KEY

# from backend/  (all commands below assume this cwd)
cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000
python manage.py qcluster                                 # second terminal: imports + reminders
python manage.py seed_demo_data                           # realistic demo content
```

`manage.py` defaults `DJANGO_SETTINGS_MODULE` to `config.settings.dev`, so no export is needed
locally. Production uses `config.settings.prod` and **fails fast** without `DATABASE_URL` and
`ALLOWED_HOSTS`.

Python 3.12+ (repo venv: 3.13.7). PDF routes additionally need WeasyPrint's native Pango/GTK
libraries; without them those routes answer a deliberate plain-text **503**.

---

## 2. Layout

```
backend/
├── manage.py
├── config/
│   ├── settings/{base,dev,prod,test}.py   # base = shared; never imported directly
│   ├── urls.py                            # root URLconf; every app registers its own namespace
│   └── wsgi.py, asgi.py
├── apps/
│   ├── core/          # shared foundation: BaseModel, role mixins, middleware, audit, money,
│   │                  #   pdf, exports, events, notifications, health, SchoolSettings, management commands
│   ├── accounts/      # custom User + role field, profiles, portals, login/logout, staff admin
│   ├── academics/     # AcademicYear, Term, SchoolClass, Stream, Subject, ClassSubject
│   ├── students/      # Student, StudentGuardian, BulkImportJob, PromotionBatch/Record
│   ├── teachers/      # TeacherProfile, TeacherAssignment, marks entry views
│   ├── results/       # Exam, GradeScale(+Bands), ResultSheet state machine, ScoreEntry, remarks, PDFs
│   ├── finance/       # the ledger: 1,700-line models.py, billing, payments, approvals, periods
│   ├── homework/      # Homework, attachments, submissions
│   ├── attendance/    # AttendanceRegister/Entry (+ services, selectors) — UI still a stub
│   ├── communications/# Announcement(+Read), MessageThread, ThreadMessage
│   ├── dashboards/    # six role homes + the Director analytics suite
│   ├── learning/      # Virtual School: course builder, lessons, quizzes, class feed, points/levels,
│   │                  #   streaks, student notes, Markdown rendering — see §5a below
│   ├── public/        # the school's open website at / and its editing screens at /website/
│   └── public_site/   # DORMANT — not in INSTALLED_APPS, not routed. Dead code.
├── tests/             # cross-app integration, e2e role matrix, static-asset contract
├── requirements/      # base.txt / dev.txt / prod.txt
├── pytest.ini         # DJANGO_SETTINGS_MODULE = config.settings.test
├── media/             # uploads (gitignored)
├── staticfiles/       # collectstatic output (gitignored)
└── db.sqlite3         # development database (gitignored)
```

### The layering rule (non-negotiable)

```
template  →  view (permissions + orchestration only)
              ├── selectors.py   READS only. Never writes.
              └── services.py    WRITES only. @transaction.atomic, audit log, events, cache invalidation.
```

Views hold **no business rules**. The same service is used by the browser, a management command and a
background task, so a rule cannot be bypassed by choosing a different entry point. Forms carry
validation that belongs to a form; anything with money consequences lives in `services.py`.

Two of these rules are enforced by tests rather than convention:
- **Only Finance calculates money.** `apps/dashboards` may not import `apps.finance.models`,
  `apps.finance.services` **or `apps.finance.selectors`**, may not do money arithmetic, and may not
  import `openpyxl` or `weasyprint` — `FORBIDDEN_MODULES` and the AST walk in
  [`apps/dashboards/tests/test_director_finance_contract.py:35`](apps/dashboards/tests/test_director_finance_contract.py#L35).
- **`apps/finance/interface/` is the money façade.** Other modules ask Finance for figures through that
  package and nothing else. Its own docstring states the split and why:
  `interface` = the contract (names, argument shapes, return shapes) · `selectors` = the queries that
  satisfy it, reachable only inside `apps/finance` · `services` = the writes, never reachable from a
  screen ("a figure that can also change the ledger while rendering it is not a figure, it is a side
  effect"). The package holds **no arithmetic** by design — a second copy of the maths is what once put
  the Director's "outstanding" and the Finance officer's "outstanding" two hundred cedis apart over a
  waived fee, with both screens internally consistent and unreconcilable.
- **The ledger is append-only** — see §6.

---

## 3. Settings

| File | Purpose |
|---|---|
| `config/settings/base.py` | Everything shared: paths, apps, middleware, template config + context processors, auth, cache, static/media, logging, Axes, DRF, Django-Q, security-header defaults. Reads `<root>/.env` via `django-environ`. |
| `config/settings/dev.py` | `DEBUG`, django-debug-toolbar, relaxed hosts. |
| `config/settings/prod.py` | Postgres required, `CONN_MAX_AGE=60`, `CONN_HEALTH_CHECKS`, forced HTTPS/HSTS/secure cookies, `SECURE_PROXY_SSL_HEADER`, JSON logging default, Sentry when `SENTRY_DSN` is set (`send_default_pii=False`). |
| `config/settings/test.py` | In-memory SQLite, Axes off, MD5 password hashers, locmem cache, plain static storage, 60s KPI cache TTL. |

Paths worth knowing: `BASE_DIR = backend/`, `PROJECT_ROOT = backend/..`,
`FRONTEND_DIR = PROJECT_ROOT/"frontend"`, `TEMPLATES.DIRS = [FRONTEND_DIR/"templates"]`,
`STATICFILES_DIRS = [FRONTEND_DIR/"static"]`, `STATIC_ROOT = backend/staticfiles`,
`MEDIA_ROOT = backend/media`.

**No app has its own `templates/` directory** — every template is in `frontend/templates/`, which is
why the front end is a sibling of the Python package rather than a separate service.

### Context processors (`apps/core/context_processors.py`)
`base_template` (picks the role shell for the current request), `school_settings` (identity,
currency, `show_positions`, `fees_enabled`, logo — falls back to the `SCHOOL_*` settings when a field
is blank), `navigation` (the sidebar tree), `parent_children`, `unread_count` (the navbar badge).

### Middleware order (`base.py:82`)
`SecurityMiddleware` → `WhiteNoise` → `Session` → `Common` → `CsrfView` → `Authentication` →
**`ForcePasswordChangeMiddleware`** → `Messages` → `XFrameOptions` → **`AxesMiddleware`** →
**`RequestIDMiddleware`** → **`RequestTimingMiddleware`**. The three custom entries are the project's
own, in `apps/core/middleware.py`.

### Installed apps
Django contrib (7, incl. `humanize`) · third-party: `rest_framework`, `django_q`, `axes` · local: the
12 listed in §2 (`public_site` excluded deliberately).

`ATOMIC_REQUESTS = True` — every request is a transaction, so a crash mid-write cannot leave a
half-posted journal.

---

## 4. Access control

### The mechanism
One mixin family in [`apps/core/mixins.py`](apps/core/mixins.py). `RoleRequiredMixin(LoginRequiredMixin)`
compares `request.user.role` against `allowed_roles` and raises `PermissionDenied`.

`AdminRequiredMixin`, `DirectorRequiredMixin`, `TeacherRequiredMixin`, `StudentRequiredMixin`,
`FinanceRequiredMixin`, `ParentRequiredMixin`, `AdminOrDirectorMixin`, `StaffMixin`
(admin/director/teacher/finance), `SchoolReadMixin` (admin + director + finance, **reads only** — every
write view in academics/students/teachers keeps `AdminRequiredMixin`).

App-local variants: `FinanceReadMixin` / `FinanceWriteMixin` (`apps/finance/views.py:64,68`),
`StudentOrParentMixin` (`apps/homework/views.py:37`, `apps/results/views.py:39`), `HomeworkFileMixin`
(attachment streaming), `WebsiteAdminMixin` (`apps/public/views_admin.py:30`), and
`PublicPageMixin` (`apps/public/views.py:30`) which is **deliberately unauthenticated**.

There is **no** `@permission_required`, no `user_passes_test`, no custom `AdminOnly` decorator anywhere
— one mechanism, one file to audit.

Behaviour:
- Wrong role → **403 with an explicit message**, never a silent redirect (a redirect tells an attacker
  the URL exists).
- Superuser bypass applies **only when the account has no role**. A role-tagged superuser is held to
  its role — pinned by a test.
- The authoritative mapping of URL → allowed role is `URL_ROLE_MATRIX` in
  [`apps/core/tests/test_security_matrix.py:63`](apps/core/tests/test_security_matrix.py#L63), expanded
  across the whole URLconf by `backend/tests/e2e/test_role_matrix.py`. Add a route without adding it to
  the matrix and the suite fails.

### Identity
`AUTH_USER_MODEL = accounts.User` — a single `AbstractUser` table with a `role` CharField
(`admin`/`director`/`teacher`/`student`/`finance`/`parent`), unique email, avatar, phone, and
`must_change_password`. Role profiles: `AdminProfile`, `DirectorProfile`, `FinanceProfile` (each with a
`staff_id`), `ParentProfile` (occupation, address, national ID; children reached through
`students.StudentGuardian`). Teacher and student profiles live in their own apps.

Login is resolved by `apps/accounts/backends.EmailOrAdmissionBackend`: **staff by email, students by
admission number, parents by username.** Portals come from one `PORTALS` registry
(`apps/accounts/portals.py`) and render `/login/` (chooser) and `/login/<slug>/`.
`/dashboard/` is a stub that routes a logged-in user to their role home.

First-login password change is enforced twice — by the login view and by
`ForcePasswordChangeMiddleware` with an exempt-prefix list. Staff (admin/director/teacher/finance)
cannot self-reset: `config/urls.py:23` installs `StaffBlockedPasswordResetView` **before** the
`django.contrib.auth.urls` include, and the admin sets a new staff password at
`/accounts/staff/<pk>/reset-password/` (the admin types it; nothing is generated). Logout is POST-only.

### Row-level checks
Ownership is resolved inside views/services, never trusted from a querystring: student identity comes
from the login; parents are scoped through `parent_profile.children_links`; a parent's "viewing as
child" selection is session-scoped (`accounts:select_child`); report cards resolve through
`_resolve_student_for_results` and serve only **published** sheets; homework file views scope by the
teacher's own assignments (`_scoped_homework()`).

---

## 5. App reference

### `core` — shared foundation
Models: `BaseModel` (abstract `created_at`/`updated_at`, inherited by everything), `AuditLog`
(immutable: actor, action verb like `finance.payment_reversed`, model label + object ID, JSON
`change_summary` diff, `request_id`, `ip_address`), `SystemEvent` (human-rendered business events for
the activity feed and daily report), `Notification`, and `SchoolSettings` (singleton pk=1: name, motto,
phone, email, currency, `show_positions`, `fees_enabled`, plus website logo/address/established/
about/mission/vision).

Modules: `money.py` (the one `to_money()` rounding rule + `currency_code()`), `audit_log.py`
(`log_action(...)` — called explicitly from services, **not** signal-driven), `events.py` +
`business_events.py`, `notifications.py`, `exports.py`, `pdf.py`, `health.py`, `middleware.py`,
`navigation.py`, `features.py` (the module on/off registry: a flag is a boolean column on
`SchoolSettings` named in `FEATURES`, never inferred from whether data exists — a switched-off module
renders `feature_disabled_message()` as a sentence rather than a zero, because an empty card reads as
"the school owes nothing"), `validators.py`, `context_processors.py`, `mixins.py`,
`templatetags/core_extras.py` (`dict_get`, `{{ value|money }}`, `{{ value|money_digits }}`).

Routes: `/healthz/` (liveness, always 200) · `/readyz/` (readiness: DB `SELECT 1` + cache set/get,
200 or 503) · `/audit/log/` · `/school/` · `/activity/feed/` and `/activity/daily-report/` — the last
three Admin-only HTML views.

Management commands (the project has exactly two of its own):
- `python manage.py seed_demo_data` — `apps/core/management/commands/seed_demo_data.py`. Flags
  `--students-per-class N` (2–100, default 10), `--dry-run`, `--force`. Idempotent by natural key,
  fixed RNG (`20260901`), writes through the service layer so audit and ledger trails look real. Seeds
  no attendance. Refuses to run under prod settings without `--force`.
- `python manage.py seed_statement_scenarios` — `apps/finance/management/commands/seed_statement_scenarios.py`.
  Builds nine pupils in a **dedicated class** so no real pupil is billed by it, one year, three terms and
  a receipt history covering every per-term statement state at least once (settled, part-paid with
  carry-over, untouched, paid ahead, late, awarded, reversed, draft, holding a credit). Also idempotent,
  also service-layer only, and prints a per-term figures table at the end that is the acceptance sheet
  for statement work.

### `accounts`
`/login/`, `/login/<role>/`, `/logout/`, `/dashboard/`, `/profile/`, `/my-children/`,
`/my-children/select/`, `/staff/`, `/staff/add/`, `/staff/<pk>/` (+ deactivate, reactivate,
reset-password — all Admin-only).

### `academics` — `/academics/`
`AcademicYear` (dates, singleton "current" enforced in `save()`), `Term` (term_1/2/3 per year, current
flag), `Subject` (code/name), `SchoolClass` (grade within a year, `level_order` for promotion, FK
`class_teacher` → a teacher-role User, nullable FK `grade_scale` → `results.GradeScale`), `Stream`,
`ClassSubject`. Lists are `SchoolReadMixin`; every mutation is `AdminRequiredMixin`. Year and term
lifecycle is covered (`years/<pk>/`, `set-current/`, `delete/`, `terms/<pk>/edit/`), but **classes,
streams and subjects are create-only** — corrections there go through `/django-admin/`, which registers
only academics and accounts.

### `students` — `/students/`
`Student` (OneToOne user, unique `admission_number`, current class/stream, status
active/repeating/graduated/withdrawn, sponsor + payment-plan notes), `StudentGuardian` (through table
with `relationship` and `is_primary`), `BulkImportJob` (Excel upload; pending → validating → ready →
committed/failed; `all_or_nothing` vs `partial`; stored preview and error rows), `PromotionBatch`
(from-year → to-year, gating term, `override_unpublished` + reason, preview snapshot, **unique
`idempotency_key`**), `PromotionRecord` (promote/repeat/graduate per pupil).
Routes: list, detail, `add/`, `<pk>/pdf/`, `admission-form/`, `import/` (+ template, preview, error
report), `promote/` (+ history). Column headers come from
`apps.students.services.BULK_TEMPLATE_HEADERS` — `scripts/make_student_import_xlsx.py` imports that
same list so fixtures can't drift from the importer.

### `teachers` — `/teachers/`
`TeacherProfile` (OneToOne user, unique `staff_id`, qualification, department), `TeacherAssignment`
(teacher ↔ class ↔ subject ↔ academic year, optional stream narrowing) with a **DB-level unique
constraint enforcing one teacher per class-subject per year**. Routes: list, detail, assignment CRUD,
`matrix/`, distribution, `workload/`, set-class-teacher, and teacher-facing `my-classes/`, `marks/`,
marks entry (which writes into results sheets).

### `results` — `/results/`
`Exam` (one of the ordered exam events in a term; unique on `(term, order)` and `(term, name)`),
`GradeScale` (OneToOne per academic year) + `GradeScaleBand` (bands must start at 0, end at 100, no
overlap or gap, unique labels — saving re-grades everything), `GradingConfig` (pass mark per year),
legacy `GradeBand` (fallback path for pre-scale years), `ResultSheet`, `ScoreEntry` (raw score 0–100,
mirrored total, grade/remark/points, unique per sheet+student), `ClassTeacherRemark` (one free-text
remark per student+term, printed on the report card).

**State machine:** `Draft → Submitted → Confirmed → Published`, driven by an explicit
`VALID_TRANSITIONS` dict and `apps/results/services.py::_transition`, which validates the move, writes
exactly one `AuditLog` row, and stamps `confirmed_by`/`published_by`. Submitted → Draft reopens before
confirmation; Confirmed → Published, or reopen **with a non-blank reason**; Published → Draft is rare
and audited. `is_locked` is true once Confirmed. Students and parents only ever see Published.

Routes: `my-results/`, report-card PDF, student/parent result views, and Admin governance —
`/results/sheets/`, sheet detail, confirm, publish, reopen, publish-class-term, broadsheet (+ broadsheet
PDF), grading config list/edit, grade scale edit/delete.

### `finance` — `/finance/`
See §6.

### `homework` — `/homework/`
`Homework` (subject/class/stream/term, `set_by` teacher, due date, `draft` → `published` with
`published_at`; one legacy `attachment` plus many `HomeworkAttachment`, capped at 5 by
`MAX_*` constants), `HomeworkSubmission` (unique per homework+student, note, `submitted_at`, `is_late`
frozen at submission time), `HomeworkSubmissionAttachment` (same cap). Files upload under UUID names via
`homework_upload_path` and stream only through `HomeworkFileMixin` views. Teacher routes
(`teacher_views.py`): list/new/edit/delete/**publish** (one POST view publishes *or* unpublishes via
`publish_homework`) /preview/submissions. Student+parent routes: list, `<pk>/`, `<pk>/submit/`.

### `attendance` — `/attendance/`
`AttendanceRegister` (unique class+date, refuses future dates, `recorded_at` set only on first write so
later edits don't pollute "filled on time"), `Entry` per student with status
present/absent/late/excused, denormalised `date`, and `ATTENDED_STATUSES = (present, late)` — an
excused absence **counts against** the rate; `summarise()` returns `None` rather than `0` when there is
no data. Services and selectors exist. **The UI does not:** `apps/attendance/urls.py` exposes one route
to `ComingSoonView`.

### `communications` — `/communications/`
`Announcement` (audience everyone / by-role via `target_roles` JSON / by-class M2M; `publish_at`
scheduling, `expires_at`, `is_published`, attachment; fan-out capped at 500 recipients in
`services._notify_announcement_audience`), `AnnouncementRead`, `MessageThread` (type complaint /
inquiry / satisfaction; `open → in_progress → resolved`; `sender` + optional `on_behalf_of` student;
`assigned_to` restricted to admin/director; assignment auto-moves Open → In Progress), `ThreadMessage`
(replies, `is_opening`, attachments). Routes: announcements list/mark-read/mark-all-read, staff create,
`messages/new/`, `messages/`, `messages/<pk>/`, and Admin/Director `inbox/` + `inbox/<pk>/`.

### `learning` — `/learning/` (Virtual School) — §5a

The LMS is built on top of existing school data — classrooms are computed from `TeacherAssignment` + `ClassSubject` + `Term`, so a teacher's online classroom appears automatically with no extra setup.

**Models (8 migrations, 0001–0008):**
`CourseModule`, `Lesson` (notes with `notes_format` plain/markdown, `video_url`, up to 5 `LessonAttachment` rows), `LessonProgress`, `LessonAttachment`, `ClassEngagementSettings`, `LearningStreak`, `ClassPost`+`ClassPostReaction` (class feed), `PointConfig`+`PointLedger`+`StudentLevel` (points and levels), `StudentLessonNote` (private per-student), `Quiz`+`QuizQuestion`+`QuizChoice`+`QuizAttempt`+`QuizAttemptAnswer` (quizzes).

**Services and selectors:**
`course_services.py` (module/lesson CRUD, reorder, duplicate, copy course), `engagement_services.py` (lesson completion → streak refresh), `feed_services.py` (create/remove/pin posts, reactions), `points_services.py` (award_points, reverse_award, teacher_bonus, check_level_up), `quiz_services.py` (create/publish quiz, add questions/choices, edit-lock after submissions, start_or_resume_attempt, save_answer, submit_attempt with server-side marking, auto_submit_timed_out), `lesson_notes_services.py`, `markdown_utils.py` (safe Markdown→HTML via `markdown`+`bleach`), `video_utils.py` (parse_video_url: all YouTube formats + Vimeo + Drive; uses `youtube-nocookie.com`).

**Feature flags** (all `SchoolSettings` Booleans, all default OFF):
`learning_enabled` → gates all LMS pages · `engagement_enabled` → gates progress/streaks/points (also requires per-class `ClassEngagementSettings.is_active`) · `quizzes_enabled` → gates quiz builder and student quiz flow.

**Security rules:**
- Students can only access classrooms for their own class.
- Quiz correct answers (`is_correct`) are **never sent to the browser before submission**.
- Quiz questions are locked after the first submitted attempt (edit-lock with Duplicate-quiz escape hatch).
- Student lesson notes are private: visible only to that student.
- Class feed posts and reactions are class-scoped and teacher-moderated.
- All LMS files served through permission-checked streaming views, never via MEDIA_URL.

### `dashboards` — `/dashboards/<role>/` `models.py` is an intentional stub. The Director suite (`views_director.py`) carries
class performance and per-class drilldown, class fee overview and debtors, staff directory, "Books of
Account" (overview/ledger/trial balance), Excel exports via `core.exports` and a books PDF via
`core.pdf`. `cache.py::invalidate_school_kpis` and `selectors.py` hold the KPI aggregation.

### `public`
`apps/public/urls.py` owns the **site root** (`/` is the public home page — no redirect for anonymous
visitors), plus `home/`, `about/`, `programme/` (→ `AcademicsView`), `gallery/`, `contact/` (a FormView
creating `ContactMessage`). Content is assembled from tables the school already fills
(`SchoolSettings`, `SchoolClass`, `Subject`, `Term`) plus its own two models, `GalleryPhoto`
(publish flag, display order) and `ContactMessage` (`handled` flag + timestamp).
`apps/public/urls_admin.py` is mounted at `/website/` — gallery add/edit/publish/delete and enquiry
list/detail/handled/delete — behind `WebsiteAdminMixin(AdminRequiredMixin)`, so a visitor's `/gallery/`
and an admin's `/website/gallery/` never collide.

### `public_site`
`views.py` + `urls.py` with five near-duplicate public TemplateViews. **Not in `INSTALLED_APPS`, not in
`config/urls.py`, no models, no migrations.** Unreachable dead code superseded by `apps/public`.

---

## 6. The finance engine

`apps/finance/models.py` is ~1,720 lines and opens with the design rules; read it before changing
money behaviour.

**Money is `Decimal(14,2)` and is quantized only through `apps.core.money.to_money()`.**

### Chart of accounts
`Account` with `Type` = asset / liability / equity / income / expense, `NormalSide` = debit/credit,
unique `code`, optional `parent` (same-type grouping only, checked in `clean()`), `is_contra`,
`is_system` (immovable), `is_active`. The seeded chart is declared in `chart_of_accounts.py` and kept in
sync with the data migration `0005_seed_chart_of_accounts.py` by a test:

`1000` Cash · `1010` Bank · `1020` Mobile Money · `1100` Fees Receivable · `2100` Student Credit
Balances (liability) · `4000` Fee Income · `4010` Other Income · `5000` Discounts & Scholarships
(income/**debit** — income given up, netted inside income, not an expense) · `6000` General Expenses.

### Billing
`FeeStructure` (class+term+version, unique, `copied_from` lineage) → `FeeItem` (label, amount,
`compulsory`, own due date) → `StudentFeeElection` (per-pupil in/out of an optional item) →
`FeeDiscountRule` (percent/fixed, whole-invoice or per-item, `siblings_only`, named-students M2M,
validity window, reduction type pinned to discount/scholarship/waiver) → `PaymentPlan` +
`PaymentPlanInstallment` (**a promise, not a payment** — posts no journal entry; one running plan per
student+term by conditional uniqueness; overdue is computed by comparing plan against ledger) →
`FeeStructureUploadJob` (Excel: pending → validating → ready → committed/failed).

`Invoice` (draft/posted/cancelled; unique `(student, term, fee_structure)`; `amount_paid` is
`self.allocations.total()`; `amount_outstanding = total − paid`) and `InvoiceLine`, which snapshots each
item's label, amount and due date at posting so a later fee-structure edit never moves a billed
deadline.

### Money movement
`Payment` — method bank/cheque/mobile_money/other (**no cash option**: fees are not collected in cash;
the Cash account is petty cash the school spends), unique `reference`, unique optional
`idempotency_key` (a double-clicked form returns the first payment), unique `receipt_number` from the
series, unique `verification_code` printed on the receipt, `settled_into` the asset account (stored, not
re-derived at reversal), `print_count`/`last_printed_at` so the second copy is stamped DUPLICATE, and
`is_reversed` / `reversal_reason` / OneToOne `reverses`.

`PaymentAllocation` (receipt → invoice, unique payment+invoice). **`AllocationQuerySet.live()` filters
`payment__is_reversed=False`, and `.total()` is the single stated place where reversals net out of every
money figure.** Reversal never deletes allocation rows.

`JournalEntry` (append-only header: unique reference, `entry_date`, `source_type` ∈
invoice/payment/reversal/adjustment/expense/credit/manual + `source_id`, `posted_by`, OneToOne
`reverses`) and `JournalLine` (one leg: account, debit_amount, credit_amount, with DB check constraints
that each is ≥ 0 and that they are **not both** > 0). `_post_journal_entry()` in services is the *only*
creator of journal rows: it asserts `SUM(debit) == SUM(credit)` before any INSERT, quantizes, and refuses
closed-period or backdated postings.

`Adjustment` (discount/scholarship/waiver/write_off/other against a student, optionally one invoice;
status pending/posted/rejected — pending holds no ledger line, and `AdjustmentQuerySet.live()` = POSTED
so a pending write-off never lowers a balance). `Vendor` (deliberately not a User). `Expense` (account,
vendor or payee name, sequenced reference, pending/posted/rejected, and `paid_via` — `cash` or `bank`,
and the credit side follows the claim: `BANK` credits `1010` Bank, `CASH` credits `1000` Cash, chosen by
`_post_expense_entry` and never defaulted; `test_postings.py:275` walks every `PaymentVia` value).
`Budget` (one number per term+expense account; variance is never stored — it is journal
debits minus this number).

`FinancialPeriod` (term or month, open/closed, created lazily; a posting date is checked against
**every** period containing it, so a closed month inside an open term still refuses the posting; reopen
requires Admin + reason + event). `ApprovalRequest` — the second-signature queue: kind ∈
expense/write_off/reversal/period_reopen, mapped to exactly one target row via `TARGET_BY_KIND`
(validated in `clean()`), one *pending* ask per target (conditional unique constraints), and **approving
performs the action through the ordinary service**, so the queue cannot post what the posting path would
refuse. Thresholds come from `FinanceSettings.amount_needs_approval()`.

`NumberSeries` — gap-free invoice/receipt/expense numbering per kind+year, taken by **compare-and-set**
(`UPDATE ... WHERE next_number = read_value`) rather than `select_for_update`, because SQLite cannot
lock; a failed transaction hands the number back, so there are no gaps. Labels format as
`PREFIX-YEAR-00042`.

`FinanceSettings` (singleton pk=1) — document prefixes, `period_lock_enabled`, `backdating_days`,
`approval_reversal_threshold` (500), `approval_expense_threshold` (1000),
`write_off_always_needs_approval`, `reminder_throttle_days`, late-fee switches (**flags only, nothing auto-
posts**), `budget_alert_percent` (80), bank details. Admin-owned vs finance-owned fields are documented,
and changes are audit-logged.

### How a pupil's balance is computed
`finance/selectors.py`, per invoice:
`outstanding = total_amount − paid − adjusted`, where `paid` is a correlated subquery over
`PaymentAllocation...live()` (excludes reversed receipts) and `adjusted` over `Adjustment...live()`
(POSTED only). Statement level: `balance = total_billed − total_paid − total_adjusted`.

### Reversal rules (`services.reverse_payment`)
Reason required · double reversal refused · above threshold requires Admin · posts a `REV-<receipt>`
entry **mirroring the original line-by-line with DR/CR swapped** (so overpayment surplus returns to
Student Credit Balances, not Fees Receivable) · flags the original `is_reversed` · creates a reversal
`Payment` row with `amount = -payment.amount` and its own receipt number · never deletes allocations ·
audit-logged, `SystemEvent`, KPI cache invalidated.

### Money vocabulary (use these words, they are pinned by tests)
*billed* · *collected* · *adjusted* · *outstanding = billed − collected − adjusted* (waived money is
never "owed") · collection rate may be `None` and renders `—`, never a fake `0%` · "class" always means
the pupil's **current** class. Two deliberately **unmerged** bases exist: allocation-basis
`fee_totals()` and receipt-date-basis `cash_received()`, bridged by `cash_received()["unallocated"]`.
Any screen showing one must name its basis.

### Known finance gaps
No refunds · no manual-journal UI · `Invoice.Status.CANCELLED` exists but no
flow writes it · invoice-less adjustments are invisible to fee totals · **late fees are computed but go
nowhere** — `selectors.late_fee_proposals` (`apps/finance/selectors.py:1671`) builds the rows and the
`late_fees_enabled` help text (`models.py:1664`) says so itself ("nothing is posted automatically"), but
no view, template or service consumes it: the only callers are tests and the `interface` re-export ·
`Invoice.due_date` is only ever as good as the fee items it was billed from — `FeeItem.due_date` is
optional, and when the items are blank the invoice header **and every line** are `null`, so aging and
the late-fee proposal fall back to `issued_date` (`due_date or issued_date`,
`apps/finance/selectors.py:1707`). In the seeded demo database 0 of 120 invoices carry a due date ·
overpayment stays unallocated while the journal credits the full
amount · **re-billing after revising a fee structure doubles `billed`** · fee reminders ignore the
selected students and broadcast to everyone — `send_fee_reminder_task` takes `student_ids`, logs them,
then calls `create_announcement(audience="by_role", target_roles=["student", "parent"])` ·
`fees_enabled` hides dashboard figures and feature-tagged sidebar entries, but does **not** gate the
`/finance/` screens or the ledger.

---

## 7. URL map

| Prefix | Namespace | Auth | Contents |
|---|---|---|---|
| `/` | `public` | anonymous | Home, `about/`, `programme/`, `gallery/`, `contact/` |
| `/website/` | `public_admin` | Admin | Gallery CRUD/publish, enquiry moderation |
| `/login/`, `/login/<role>/`, `/logout/`, `/dashboard/`, `/profile/`, `/my-children/`, `/staff/` | `accounts` | mixed | Portals, redirect stub, staff management |
| `/accounts/…` | contrib auth | mixed | Password reset/change — **`/accounts/password_reset/` is overridden** by `StaffBlockedPasswordResetView` before the include, and `/accounts/login/` points at the portal chooser |
| `/academics/` | `academics` | SchoolRead / Admin | Years, terms, classes, streams, subjects, class-subjects |
| `/students/` | `students` | SchoolRead / Admin | Roster, profile, admission PDFs, bulk import, promotion |
| `/teachers/` | `teachers` | SchoolRead / Admin / Teacher | Profiles, assignments, matrix, workload, my classes, marks |
| `/results/` | `results` | mixed / Admin | `my-results/`, report-card PDF, `sheets/`, governance actions, broadsheet, `grading/` |
| `/finance/` | `finance` | FinanceRead / FinanceWrite | `overview`, `accounts/`, `fee-structures/` (+ upload/preview/commit/template), `invoices/` (+ generate), `payments/` (+ record/receipt/reverse), `adjustments/create/`, `ledger/`, `reports/{trial-balance,summary,classes,outstanding(+export)}`, student statements (HTML+PDF), class/school summaries, `expenses/`, `reminders/`, `my-statement/` |
| `/homework/` | `homework` | Student/Parent + Teacher | List, detail, submit; teacher CRUD + publish + preview + submissions |
| `/attendance/` | `attendance` | LoginRequired | **Stub** — `ComingSoonView` only |
| `/learning/` | `learning` | mixed | Virtual School: classrooms, course builder, lessons, quizzes, class feed, progress, badges, points — 35+ named routes. Gated by `learning_enabled` / `engagement_enabled` / `quizzes_enabled` flags. |
| `/communications/` | `communications` | mixed | Announcements, message threads, admin inbox |
| `/dashboards/<role>/` | `dashboards` | per role | Six role homes + Director analytics |
| `/audit/log/`, `/school/`, `/activity/` | `core*` | Admin | Audit viewer, School Settings, activity feed + daily report |
| `/healthz/`, `/readyz/` | `core` | anonymous | Liveness (always 200) / readiness (DB + cache, 503 on failure) |
| `/django-admin/` | contrib | is_staff | Registers only academics + accounts. Move to an unguessable path in production. |
| `/__debug__/` | toolbar | DEBUG only | django-debug-toolbar |

Media files are served by Django only when `DEBUG`.

---

## 8. Background jobs

`django-q2` on the ORM broker — no Redis, no RabbitMQ. `Q_CLUSTER`: 2 workers, 300s timeout, 360s retry,
queue limit 50, bulk 10, `catch_up=False`.

| Task | Module | Trigger |
|---|---|---|
| `validate_bulk_import` | `apps/students/tasks.py` | Student `.xlsx` upload → preview |
| `validate_fee_structure_upload` | `apps/finance/tasks.py` | Fee-structure `.xlsx` upload → preview |
| `send_fee_reminder_task` | `apps/finance/tasks.py` | `/finance/reminders/` |

Run `python manage.py qcluster`. Without it these jobs queue silently.

---

## 9. Files, PDFs and exports

- **`apps/core/pdf.py`** — `pdf_response()` renders a template through **WeasyPrint**. When WeasyPrint or
  its native libraries are missing it returns a plain-text **503**; it never returns HTML named `.pdf`.
  `xhtml2pdf` is in requirements but imported nowhere. PDF templates: report card, class broadsheet,
  payment receipt, student statement, Director books report, admission form (+blank).
- **`apps/core/exports.py`** — the only sanctioned spreadsheet path, including the **formula-injection
  defence**: any cell text starting `= + - @ TAB CR` is forced to `data_type="s"`. Also
  `stamp_document()` provenance.

---

## 10. Observability

Per [ADR-004](../docs/decisions/observability.md):
- `RequestIDMiddleware` mints a UUID, keeps it in `threading.local()` (cleared after each response),
  echoes `X-Request-ID`, injects it into every log record and onto every `AuditLog.request_id`.
- `RequestTimingMiddleware` warns above `SLOW_REQUEST_THRESHOLD_MS` (500) with path, method, elapsed ms,
  user, request ID.
- Logging: `LOG_FORMAT=json` for `python-json-logger` (prod default), `verbose` in dev —
  `[time] LEVEL name | rid=… uid=… | message`. `apps.events` is a separate business-event stream
  (`payment_posted`, `payment_reversed`, `invoices_posted`, `results_published`) routable to
  `/var/log/school/events.jsonl` without code changes. `django.db.backends` is muted to WARNING.
- Three audit paths, three questions: `AuditLog` = *what changed on this object*;
  `SystemEvent` + structured log = *what happened at school today* (written in a savepoint, never raises,
  may aggregate); `business_events.log_event` = machine-greppable finance line events. Only invoice
  posting, payment recording/reversal and `results.published` reach the activity feed; adjustments,
  expenses, fee structures and enrolment are audit-only.
- Database timestamps are UTC (`USE_TZ = True`) while `TIME_ZONE = "Africa/Nairobi"` (EAT, UTC+3) governs display — Kenyan time. Changed from `Africa/Accra` during the LMS build; streak day-boundary calculations depend on this. `DjangoIntegration` + `LoggingIntegration` (INFO breadcrumbs,
  ERROR events), 10% tracing, `send_default_pii=False`.

---

## 11. Tests, lint, migrations

```bash
cd backend
python -m pytest -q                       # ~1,566 tests, 80+ files — see backend/pytest.ini
python -m pytest apps/finance -q          # one app
python -m pytest tests/e2e -q             # role matrix across the whole URLconf
python -m pytest --cov=apps --cov-report=term-missing
python -m ruff check apps config tests
python manage.py check
python manage.py makemigrations --check --dry-run
```

`pytest.ini` sets `DJANGO_SETTINGS_MODULE = config.settings.test`, `-v --tb=short`, and ignores
DeprecationWarnings. Coverage of a run is local only (`.coverage` is gitignored).

**Migration policy: add new migrations only; never edit or delete an applied migration** — including the
chart-of-accounts data migration and the SQLite append-only triggers. Chart changes ship as a new
migration plus the test that keeps `chart_of_accounts.py` in sync.

Architecture tests to know about: `apps/core/tests/test_security_matrix.py` (URL→role),
`tests/e2e/test_role_matrix.py`, `tests/test_static_assets.py` (offline vendor contract),
`apps/dashboards/tests/test_director_finance_contract.py` (money-boundary AST guard),
`apps/finance/tests/test_accounts_seed.py`, `apps/core/tests/test_navigation_integrity.py`
(`KNOWN_BROKEN` is currently `{}` — every sidebar link resolves), `test_director_query_count.py`.

Query budgets pinned by tests: `school_kpis` ≤ 12, `class_fee_summary` ≤ 5, Director home ≤ 20 cold /
≤ 12 warm. Aggregation is done in Python on scoped querysets rather than multi-join `.annotate()`,
because aggregation across joins fans rows out and produces wrong totals.

---

## 12. Performance and scaling

Composite indexes: `accounts_user(role,is_active)`, `finance_invoice(term,status)`,
`finance_invoice(student,status)`, `finance_payment(student,payment_date)`,
`finance_journal_entry(source_type,source_id)`, `students_student(current_class,status)`,
`students_guardian(parent,is_primary)`.

Cache: one key, `director_kpis_term_{term.pk}`, TTL `DIRECTOR_KPI_CACHE_TTL` (600s), invalidated only by
`record_payment`, `reverse_payment`, `publish_sheet`. In production set `CACHE_URL` to Redis or each
Gunicorn worker keeps a private locmem copy and the Director dashboard will disagree between workers.

`prod.py`: `CONN_MAX_AGE=60`, `CONN_HEALTH_CHECKS=True`, `ATOMIC_REQUESTS`, WhiteNoise compressed
manifest static storage. Gunicorn `--workers 3` (rule of thumb `2×cores+1`) behind nginx with TLS + gzip.
The app is stateless **except `MEDIA_ROOT`** — shared storage before adding a second node.

Interpretation thresholds from `docs/performance.md`: p50 ≤ 100 ms is fine for a LAN tool, p95 ≤ 500 ms
warns (these are the requests `RequestTimingMiddleware` logs), p99 ≤ 1000 ms alerts, any failures > 0%
investigate immediately. Those tables are labelled indicative targets, not measurements on this
hardware.

Load test: [`../locustfile.py`](../locustfile.py) — StudentUser (w40), ParentUser (25), AdminUser (10),
DirectorUser (5), FinanceUser (5); `_login()` fetches the CSRF token from `/login/` and treats "URL no
longer contains `/login/`" as success, raising `StopUser` otherwise. Its credentials must match the
seeder.

```bash
locust -f locustfile.py --host http://localhost:8000 --headless --users 50 --spawn-rate 5 --run-time 60s
```

Backups: `bash scripts/backup_db.sh` (`pg_dump --format=custom --compress=9`, or SQLite's online
`.backup`), retention via `KEEP_DAYS`. Restore: `bash scripts/restore_db.sh <file>` — destructive,
prompts `yes`, keeps a `.pre_restore_<timestamp>` safety copy for SQLite, terminates Postgres
connections before `DROP/CREATE DATABASE`, prints a post-restore checklist.

---

## 13. Gotchas for anyone working in here

1. Run Python tooling **from `backend/`** — `pytest.ini` and the settings default live there.
2. Tests never touch `backend/db.sqlite3`; they build an in-memory DB. A local bug caused by unapplied
   migrations will not show up in CI-less pytest.
3. Adding a URL without adding it to `URL_ROLE_MATRIX` fails the security suite — that is intended.
4. Do not compute money outside `apps/finance/services.py`; the dashboards AST guard will fail the run.
5. Never `update()`/`delete()` a journal row — the Python guards raise and, on SQLite, DB triggers do it
   too. Write a reversing entry.
6. Re-downloading a vendored asset breaks `collectstatic` (source-map comments) and
   `tests/test_static_assets.py`.
7. `scripts/gen_stubs.py` and `scripts/write_finance_models.py` are historical one-shot generators that
   **overwrite live code** — `write_finance_models.py` also contains a hard-coded absolute machine path.
   Do not run them.
8. `apps/public_site/` is not installed; editing it changes nothing user-visible.
9. `docs/SYSTEM_GUIDE.md` is the reference but has drifted in three places — the finance UI (30
9. `docs/SYSTEM_GUIDE.md` is the reference but has drifted — the finance UI, results entry and test count are all stale. Trust the tree and these READMEs.
10. There is no CI and no pre-commit config; `black`/`pre-commit` are declared dependencies that nothing
    runs. `manage.py check` + `ruff` + `pytest` + `makemigrations --check` by hand, before every checkpoint.
11. The `learning` app has 8 migrations (0001–0008). Three later checkpoints add field migrations to existing models — always run `migrate` after pulling.
