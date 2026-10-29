# School Management System — how the whole thing works

A single-school management system: student records, academic structure, fees and a double-entry
ledger, results governance, homework, attendance, communications, and role-based dashboards.

This document is the operational handbook: what each part does, the exact steps for each job, and the
rules that keep the numbers honest. It describes the system **as it is in this repository today** —
including the parts that are built in code but have no screen yet.

**Status legend used throughout:**

| Mark | Meaning |
|---|---|
| ✅ | Works in the browser today (view + template + role guard all present) |
| ⚙️ | Implemented in the service/selector layer, tested, **no screen exists** — done from the Django shell or by the demo seeder |
| 🚧 | Not implemented at all |

File references are repo-relative, `path:line`, so you can jump straight to the rule being described.

---

## 1. The shape of the system

| App | Owns |
|---|---|
| `accounts` | The one `User` model (with `role`), staff management, login/password flows |
| `students` | `Student`, guardians, enrolment, bulk import, promotion |
| `academics` | `AcademicYear`, `Term`, `SchoolClass`, `Stream`, `Subject`, `ClassSubject` |
| `teachers` | `TeacherProfile`, `TeacherAssignment` (who teaches which class/subject/year) |
| `finance` | Chart of accounts, fee structures, invoices, payments, allocations, adjustments, expenses, the journal |
| `results` | Grading config, `ResultSheet`, `ScoreEntry`, the publish workflow, report cards |
| `homework` | `Homework` and its attachment downloads |
| `attendance` | `AttendanceRegister`, `AttendanceEntry`, marking service, rate selectors |
| `communications` | Announcements, message threads/complaints, inbox |
| `dashboards` | One home page per role + the Director portal |
| `core` | Audit log, notifications, `SystemEvent`, `SchoolSettings`, navigation, mixins, exports, PDF, health |

**The layering rule** every module follows:

```
template  →  view (permissions only)  →  selectors.py  (READS, never writes)
                                       →  services.py  (WRITES, @transaction.atomic, logs, emits events)
```

Views do not contain business rules. Reads never happen in a view directly against models when a
selector exists. Writes never happen outside a service, so every mutation leaves an audit trail.

**Two hard architectural rules**, both enforced by tests rather than by convention:

1. **Only Finance calculates money.** `apps/dashboards` may not import `apps.finance.models` or
   `apps.finance.services`, may not do arithmetic on anything named like money, and may not import
   `openpyxl`/`weasyprint`. The AST guard is
   [backend/apps/dashboards/tests/test_director_finance_contract.py:32](backend/apps/dashboards/tests/test_director_finance_contract.py#L32).
   Director figures come from `apps.finance.selectors` — the single derivation point.
2. **The ledger is append-only.** `JournalEntry` and `JournalLine` raise on `save()` of an existing row
   and on `delete()` ([backend/apps/finance/models.py:354](backend/apps/finance/models.py#L354),
   [:404](backend/apps/finance/models.py#L404)), backed by DB check constraints
   ([backend/apps/finance/models.py:389](backend/apps/finance/models.py#L389)). Mistakes are corrected by
   posting a reversing entry, never by editing or deleting.

**Template chain:** `frontend/templates/base.html` → `dashboards/<role>/base.html` → feature page.
The shell is chosen per request by `context_processors.base_template`
([backend/apps/core/context_processors.py:24](backend/apps/core/context_processors.py#L24)).

---

## 2. Running it

```bash
# from the repo root
python -m venv .venv
.venv\Scripts\activate                     # Windows
pip install -r backend/requirements/dev.txt
cp .env.example .env                       # set at minimum SECRET_KEY

cd backend
python manage.py migrate                   # dev settings, SQLite at backend/db.sqlite3
python manage.py createsuperuser           # see the role warning below
python manage.py runserver 8000
python manage.py qcluster                  # separate terminal — see "background jobs"
```

Tests and checks:

```bash
cd backend
python -m pytest -q                        # 459 tests, throwaway in-memory SQLite
python -m ruff check apps config tests     # config is ruff.toml at repo root
python manage.py check
python manage.py makemigrations --check    # must say "No changes detected"
```

Settings split: `config/settings/{base,dev,prod,test}.py`. `base.py` reads env vars via django-environ
(SECRET_KEY, DEBUG, ALLOWED_HOSTS, DATABASE_URL, CACHE_URL, `SCHOOL_*`, `EMAIL_*`)
([backend/config/settings/base.py:24](backend/config/settings/base.py#L24)).
`prod.py` requires Postgres via `DATABASE_URL`, enables HSTS and Sentry, defaults to JSON logs.
`test.py` turns axes off, uses MD5 hashers, LocMem cache and a 60s Director cache TTL.

**Two gotchas that bite immediately:**

- `User.role` has **no default** ([backend/apps/accounts/models.py:34](backend/apps/accounts/models.py#L34)),
  and a superuser only gets the legacy bypass **while `role` is empty**
  ([backend/apps/core/mixins.py:38](backend/apps/core/mixins.py#L38)). The moment you give a superuser
  the role `director`, they are 403'd out of every admin-only URL — pinned by
  [backend/apps/core/tests/test_security_matrix.py:314](backend/apps/core/tests/test_security_matrix.py#L314).
  Keep your day-to-day superuser role-less, and make separate role accounts for real work.
- **Password rules are off in dev.** `base.py:136` installs four validators (min length 8), but
  [backend/config/settings/dev.py:22](backend/config/settings/dev.py#L22) sets
  `AUTH_PASSWORD_VALIDATORS = []`. A password accepted in dev can be rejected in prod.

**Background jobs.** `Q_CLUSTER` uses the ORM broker with **no `SYNC` flag**
([backend/config/settings/base.py:219](backend/config/settings/base.py#L219)), so `async_task(...)`
succeeds by writing a row to `django_q_ormq` and returns. If `python manage.py qcluster` is not
running, those jobs are never executed and nothing errors. Evidence in this repo's dev database right
now: 3 queued jobs (all `apps.accounts.services._send_credentials_email`) and 0 in the success table.
Credential emails for new students, parents and staff are enqueued, not sent
([backend/apps/accounts/services.py:95](backend/apps/accounts/services.py#L95)). In dev the email backend
is console, so you only see them if the queue is drained or the enqueue call raises.

**Health:** `/healthz/` liveness (always 200) and `/readyz/` readiness (checks DB + cache, 503 on
failure) are anonymous ([backend/apps/core/health.py:26](backend/apps/core/health.py#L26)).

---

## 3. Users and roles

`User` extends `AbstractUser`, one table, one `role` field
([backend/apps/accounts/models.py:20](backend/apps/accounts/models.py#L20)).

| Role value | Label | Dashboard | Created by |
|---|---|---|---|
| `admin` | Admin | `/dashboards/admin/` | Django admin only (`/django-admin/`) — the staff UI refuses this role |
| `director` | Director | `/dashboards/director/` | Admin → Staff users → Add |
| `finance` | Finance Officer | `/dashboards/finance/` | Admin → Staff users → Add |
| `teacher` | Teacher | `/dashboards/teacher/` | Admin → Staff users → Add |
| `student` | Student | `/dashboards/student/` | Automatically, when a student is enrolled |
| `parent` | Parent / Guardian | `/dashboards/parent/` | Automatically, from the guardian email at enrolment |

Staff-only side-profiles exist as `OneToOne` rows: `AdminProfile`, `DirectorProfile`,
`FinanceProfile`, `ParentProfile` ([backend/apps/accounts/models.py:80](backend/apps/accounts/models.py#L80)).
A student's profile is the `Student` row itself (`Student.user`, related name `student_profile`,
[backend/apps/students/models.py:25](backend/apps/students/models.py#L25)); a teacher's is
`TeacherProfile` ([backend/apps/teachers/models.py:13](backend/apps/teachers/models.py#L13)).

**Parent ↔ student** is `StudentGuardian` ([backend/apps/students/models.py:71](backend/apps/students/models.py#L71)):
relationship (father/mother/guardian/other), `is_primary`, unique per (student, parent). A parent with
several children switches child via the session key `selected_child_id`
([backend/apps/dashboards/views.py:162](backend/apps/dashboards/views.py#L162)); never trust a query
parameter for identity.

**How a login is created with a student:** username = admission number lower-cased with `/` and space
→ `_`, **initial password = the admission number** unless the admin typed an override on the add-student
form ([backend/apps/students/services.py:152](backend/apps/students/services.py#L152)). Bulk import has
no override column, so a whole year group still shares the admission-number rule. The guardian account
is created or reused from `guardian_email` with a random 12-character password
([backend/apps/students/services.py:189](backend/apps/students/services.py#L189)) — there is no
parent-creation screen, so that one is still generated — and if the email already belongs to a non-parent
user, enrolment errors out rather than hijacking the account.

### Login and password flows

- Login: `POST /login/` — accepts **email or username/admission number**
  ([backend/apps/accounts/backends.py:35](backend/apps/accounts/backends.py#L35)).
- Wrong role → **403** with "Your role 'x' is not permitted…", never a silent redirect
  ([backend/apps/core/mixins.py:40](backend/apps/core/mixins.py#L40)). Unauthenticated → redirect to login.
- Lockout: django-axes, **5 failures, 1-hour cooloff**, `AXES_RESET_ON_SUCCESS`, custom JSON 403
  response ([backend/config/settings/base.py:231](backend/config/settings/base.py#L231),
  [backend/apps/core/utils.py:12](backend/apps/core/utils.py#L12)).
- `must_change_password` forces a redirect at login
  ([backend/apps/accounts/views.py:58](backend/apps/accounts/views.py#L58)) *and* a middleware catches
  already-logged-in users ([backend/apps/core/middleware.py:90](backend/apps/core/middleware.py#L90)),
  with an exempt-prefix list (login, logout, change-password, static, media, health, django-admin).
- **Staff cannot self-reset.** `admin/director/teacher/finance` are blocked from
  `/accounts/password_reset/` and get a neutral page with no email
  ([backend/apps/accounts/views.py:32](backend/apps/accounts/views.py#L32)); they must ask an admin, who
  uses Staff users → Reset password (POST-only). Students and parents can self-reset.
- Logout is **POST-only** ([backend/apps/accounts/views.py:75](backend/apps/accounts/views.py#L75)).
- Staff creation UI: `/staff/add/`, admin only, role dropdown limited to teacher/director/finance
  ([backend/apps/accounts/forms.py:57](backend/apps/accounts/forms.py#L57)). **The admin types the initial
  password** (plus a confirmation), and a "Force a password change on first login" box — ticked by default
  — decides whether it is temporary. The chosen password is echoed back in the success message
  ([backend/apps/accounts/views.py:274](backend/apps/accounts/views.py#L274)). `create_staff_user` still
  generates one when a caller omits `password`, which is only the demo seeder now. Strength is checked in
  the form via `validate_password()`, because `create_user()` never validates and dev/test set
  `AUTH_PASSWORD_VALIDATORS = []`.

---

## 4. How permission is enforced

Mixins in [backend/apps/core/mixins.py](backend/apps/core/mixins.py):

| Mixin | Allowed roles |
|---|---|
| `AdminRequiredMixin` | admin |
| `DirectorRequiredMixin` | director |
| `TeacherRequiredMixin` / `StudentRequiredMixin` / `ParentRequiredMixin` | that role |
| `FinanceRequiredMixin` | finance |
| `StaffMixin` | admin, director, teacher, finance (defined, currently unused) |
| `AdminOrDirectorMixin` | admin, director (defined, currently unused) |
| `SchoolReadMixin` | admin, director, finance — "read the structure" |

Finance splits its own surface further, because a Director may read money but never write it:
`FinanceReadMixin` = finance + admin + director, `FinanceWriteMixin` = **finance only**
([backend/apps/finance/views.py:50](backend/apps/finance/views.py#L50)).

Write-guarded finance URLs: `account_add_expense`, `fee_structure_create`, `fee_structure_upload*`,
`invoice_generate`, `payment_record`, `payment_reverse`, `adjustment_create`, `student_finance_notes`,
`expense_record`, `send_reminders`. Everything else in finance is read-guarded.

Row-level (IDOR) rules, enforced in the view and pinned by tests:

- A student asking for `?student=<other>` is ignored — identity always comes from the login
  ([backend/apps/results/views.py:54](backend/apps/results/views.py#L54)).
- A parent may only see children in `parent_profile.children_links`, else 403
  ([backend/apps/results/views.py:62](backend/apps/results/views.py#L62)).
- Report card PDFs: same owner check → 403 ([backend/apps/results/tests/test_access_and_grades.py:130](backend/apps/results/tests/test_access_and_grades.py#L130)).
- Fee statement: finance/admin/director see any pupil; student sees their own; parent their children
  ([backend/apps/finance/views.py:607](backend/apps/finance/views.py#L607)).
- Homework list and attachment downloads resolve the pupil from the session only, and re-check
  published + class + stream on the file ([backend/apps/homework/views.py:33](backend/apps/homework/views.py#L33),
  [:148](backend/apps/homework/views.py#L148)).

The authoritative access table is
[backend/apps/core/tests/test_security_matrix.py:62](backend/apps/core/tests/test_security_matrix.py#L62)
(`URL_ROLE_MATRIX`, every URL name × which roles get through) plus the generated whole-urlconf matrix in
[backend/tests/e2e/test_role_matrix.py](backend/tests/e2e/test_role_matrix.py). If you change a URL, that
test tells you which role can now reach it.

---

## 5. Sidebar map per role

Single source: `NAVIGATION` in [backend/apps/core/navigation.py:20](backend/apps/core/navigation.py:20),
rendered by `context_processors.navigation` ([backend/apps/core/context_processors.py:100](backend/apps/core/context_processors.py#L100));
a URL that no longer reverses renders `href="#"` instead of crashing.

- **admin** — Dashboard, Staff users, Academics (years/classes/subjects/class-subjects), Students
  (All / Add / Bulk import / Blank admission form / Promote class / Promotion history), Teachers,
  Results (Sheets / Grading setup), Finance, Communications (Announcements / New / Inbox), Activity
  (feed / daily report), Audit log, School settings.
- **director** — Dashboard, School analysis, Performance, Fee analysis, Staff directory, Books of
  Account (Ledger / Trial balance), Communications, Academics (read), Students (read), Teachers (read).
- **finance** — Dashboard, Ledger, Payments, Fee structures, Reports (summary / outstanding / trial
  balance), Students (read), Announcements.
- **teacher** — Dashboard, My classes, Announcements. *(That is all — see §20.)*
- **student** — Dashboard, My results, My fee statement, Homework, My messages + New complaint,
  Announcements, Profile.
- **parent** — same as student plus linked children.

Note: `SchoolSettings.fees_enabled` exists and is editable, but `NAVIGATION` has no feature-flag key and
`feature_enabled()` has no production caller ([backend/apps/core/features.py:34](backend/apps/core/features.py#L34)) —
turning fees off currently hides nothing.

---

## 6. Academic structure — the setup order

Everything else depends on this, so it is done first, in this order, all by **admin**:

| Step | URL | Role | Note |
|---|---|---|---|
| 1. Create academic year | `/academics/years/add/` | admin | `end_date >= start_date` enforced in `clean()` ([backend/apps/academics/models.py:27](backend/apps/academics/models.py#L27)) |
| 2. Set it current | `POST /academics/years/<id>/set-current/` | admin | `save()` un-flags the others — exactly one current ([backend/apps/academics/models.py:33](backend/apps/academics/models.py#L33)) |
| 3. Create terms | `/academics/terms/add/` | admin | unique (year, name) where name ∈ term_1/2/3; one current at a time |
| 4. Create classes | `/academics/classes/add/` | admin | `(year, name)` unique; **`level_order` drives promotion** |
| 5. Add class teacher | on the class page | admin | `SchoolClass.class_teacher` limited to role=teacher |
| 6. Streams | `/academics/streams/add/` | admin | unique (class, name) |
| 7. Subjects | `/academics/subjects/add/` | admin | `code` unique, `is_active` |
| 8. Map subject→class | `/academics/class-subjects/add/` | admin | partial-unique with and without stream ([backend/apps/academics/models.py:141](backend/apps/academics/models.py#L141)) |
| 9. Assign teachers | `/teachers/assignments/add/` | admin | one teacher per class+subject(+stream)+year ([backend/apps/teachers/models.py:84](backend/apps/teachers/models.py#L84)) |
| 10. Fee structures | `/finance/fee-structures/…` | ⚙️ finance role | see §8 |

"Current" means the flagged row. `get_current_year()` / `get_current_term()` return **`None` when nothing
is flagged** ([backend/apps/academics/selectors.py:16](backend/apps/academics/selectors.py#L16)) and every
dashboard and export treats that as "no data" (Director exports answer `400 No current term.`). If a page
looks suddenly empty, check the current flags before anything else.

There are **no edit or delete screens** for years, terms, classes or subjects — create + set-current only.
Corrections happen in `/django-admin/` (academics and accounts *are* registered there; students, finance,
results, homework, attendance and teachers are **not** — their `admin.py` files are stubs).

---

## 7. Students

### 7.1 Add one student ✅ (admin)

`/students/add/` → `StudentCreateView` ([backend/apps/students/views.py:65](backend/apps/students/views.py#L65)),
template `features/students/student_form.html`.

Required: first name, last name, email, **admission number**, guardian first/last name, guardian email,
relationship. Optional: date of birth, gender, class, stream, address, guardian phone, enrollment date
([backend/apps/students/forms.py:15](backend/apps/students/forms.py#L15)).

Steps:
1. Sidebar → Students → **Add**.
2. Fill the pupil block. Admission number is **operator-entered** — there is no generator and no format
   regex, only uniqueness (upper-cased and stripped in the service,
   [backend/apps/students/services.py:137](backend/apps/students/services.py#L137)). Follow your school's
   convention; it also becomes the pupil's login username.
3. Pick the class (and stream). A pupil can be enrolled with no class — they simply appear unassigned.
4. **Login block**: leave *Initial password* blank to use the admission number (the school-wide rule), or
   type a password to override it for this one pupil. The "Force a password change on first login" box is
   ticked by default; untick it when the password you typed is the one they keep.
5. Fill the guardian block. If that guardian email already has a parent account, the child is **linked to
   the existing account** instead of creating a duplicate; if the email belongs to a teacher or admin, the
   form errors and you must use a different address.
6. Submit. In one transaction you get: a `Student`, a `User` with role student (your password, or the
   admission number), a `ParentProfile` with a generated password, a
   `StudentGuardian` (`is_primary`), an audit row `student.created`, and queued credential emails
   ([backend/apps/students/services.py:115](backend/apps/students/services.py#L115)).
7. The success message shows both logins — that is the hand-out mechanism, so it is on-screen deliberately.
8. You land on the student detail page.

**Nothing about money happens here.** No invoice is generated by enrolment; billing is a separate,
explicit step (§8.2).

Known limits: the `enrollment_date` field in the form is ignored — the service always stamps today
([backend/apps/students/services.py:171](backend/apps/students/services.py#L171)); there is no date
sanity check (a DOB in the future is accepted); there is no photo field.

### 7.2 Bulk import ✅ (admin)

`/students/import/` — **`.xlsx` only**, no CSV ([backend/apps/students/forms.py:85](backend/apps/students/forms.py#L85)).

1. Download the template: `/students/import/template/` (12 columns: admission_number, first_name,
   last_name, email, gender, date_of_birth, stream, guardian_first_name, guardian_last_name,
   guardian_email, guardian_phone, guardian_relationship —
   [backend/apps/students/services.py:38](backend/apps/students/services.py#L38)).
2. Choose an optional target class and a mode: `all_or_nothing` (default) or allow partial.
3. Upload. The job goes `PENDING → VALIDATING → READY|FAILED` (async via django-q2, synchronous fallback
   if the call fails — [backend/apps/students/services.py:234](backend/apps/students/services.py#L234)).
   Without a running `qcluster` the validation task can sit queued: the page will look stuck at
   VALIDATING.
4. Preview at `/students/import/<job>/` — rows already in the school by admission number are marked
   `skip_existing` (idempotent, **not** an error, so re-running a file is safe), a taken email is an
   error, an unknown stream is an error, DOB must be `YYYY-MM-DD`, gender must be M/F/O
   ([backend/apps/students/services.py:259](backend/apps/students/services.py#L259)).
5. Commit (POST). `all_or_nothing` refuses if any row has errors. Each accepted row goes through the same
   `create_student_with_guardian` service, so guardians, logins and audit rows behave exactly as in 7.1.
6. Download the error report `/students/import/<job>/errors/` — an .xlsx of the failed rows plus their
   messages, with every cell written as literal text (see §16).

There is **no row-count limit**; a 50,000-row file will be attempted.

### 7.3 Editing, withdrawing, deleting — 🚧

There is no edit/withdraw/restore/delete URL for a student
([backend/apps/students/urls.py](backend/apps/students/urls.py) has list, add, detail, pdf, import,
promote only), and `status` can only be changed by promotion (`active`/`repeating`/`graduated`/`withdrawn`,
[backend/apps/students/models.py:47](backend/apps/students/models.py#L47)). The only pupil field editable
from any screen is Finance's `sponsor` / `payment_plan_notes` (`/finance/students/<pk>/notes/`, ⚙️).
Deletion is structurally blocked anyway: invoices, payments and adjustments `PROTECT` the pupil
([backend/apps/finance/models.py:171](backend/apps/finance/models.py#L171)), as do attendance entries — but
`ScoreEntry` **cascades**, so a delete would silently take results with it.

### 7.4 Promote a class ✅ (admin)

`/students/promote/` → preview, then commit
([backend/apps/students/views.py:251](backend/apps/students/views.py#L251),
[backend/apps/students/services.py:485](backend/apps/students/services.py#L485)).

1. Pick `from_year`, `to_year`, and the term used to check results. They must differ.
2. Preview returns, per class (ordered by `level_order`), rows of `PROMOTE` (to `level_order + 1`),
   `REPEAT` (pupils you list by admission number, or any pupil whose next class does not exist) and
   `GRADUATE` (max level with no next class), plus `results_published`.
3. **The gate:** commit refuses unless that term's results are published for every class, unless you tick
   "override" and type a reason ([backend/apps/students/services.py:618](backend/apps/students/services.py#L618)).
4. Commit writes one `PromotionRecord` per pupil, moves `current_class`, sets `status`, and **clears
   `current_stream`** ([backend/apps/students/services.py:670](backend/apps/students/services.py#L670)).
   It is idempotent: a second commit of the same `"{from}->{to}"` key is a no-op
   ([backend/apps/students/services.py:604](backend/apps/students/services.py#L604)).
5. It does **not** touch invoices, fee structures, results or guardians — a promoted pupil's existing debt
   simply follows them (class figures are computed from the pupil's *current* class).
6. History: `/students/promote/history/`.

### 7.5 Admission form PDF

`/students/<pk>/pdf/` (pre-filled) and `/students/admission-form/` (blank) ✅ exist and are permissioned,
but they call `apps.core.pdf.pdf_response`, which returns **503 plain text when WeasyPrint's native
libraries are missing** ([backend/apps/core/pdf.py:38](backend/apps/core/pdf.py#L38)). On this machine
`gobject-2.0-0` cannot load, so both answer 503 rather than handing you HTML wearing a `.pdf` name.
That is the designed behaviour.

---

## 8. Money — fees, payments, the books

This is the critical section. Read §8.1 before touching any figure.

### 8.1 The definitions everything must agree on

All money figures come from one place: the "THE MONEY READ INTERFACE" section of
[backend/apps/finance/selectors.py:515](backend/apps/finance/selectors.py#L515). The banner comment at
[:540](backend/apps/finance/selectors.py#L540) fixes the vocabulary, and the reconciliation tests assert
every screen against it.

| Term | Definition | Why it matters |
|---|---|---|
| **billed** | Σ `total_amount` of **posted** invoices in the period | Draft and cancelled invoices are not billing |
| **collected** | Σ `PaymentAllocation.allocated_amount` on those invoices | Reversals are already netted (their allocations are deleted) — do **not** filter again |
| **adjusted** | Σ `Adjustment.amount` whose invoice is in the period | A waiver is money the school gave up, not money still owed |
| **outstanding** | billed − collected − adjusted | Waived amounts are excluded, so nobody chases a forgiven debt |
| **collection rate** | collected ÷ billed × 100, or **`None`** when nothing is billed | `None` renders as `—`; a 0% on an unbilled class would be a lie |
| **"class"** | the pupil's **current** class | A class figure is not a snapshot of invoice day; a transferred pupil's balance follows them |

**Two bases, deliberately never merged** ([backend/apps/finance/selectors.py:548](backend/apps/finance/selectors.py#L548)):

- *"What did we receive in this date window?"* → `cash_received()`, counted on `Payment.payment_date`.
- *"What has this term's billing covered?"* → `fee_totals()`, counted on allocations.

They differ whenever an overpayment is unallocated or a payment settles an older term, and
`cash_received()["unallocated"]` is the bridge. `report_summary()` prints both, labelled. A screen that
shows one figure must name its basis.

**The journal.** Every money action posts a balanced entry. Chart of accounts, seeded by data migration
[backend/apps/finance/migrations/0005_seed_chart_of_accounts.py:4](backend/apps/finance/migrations/0005_seed_chart_of_accounts.py#L4)
and idempotently by `seed_accounts()` ([backend/apps/finance/services.py:136](backend/apps/finance/services.py#L136)):

| Code | Name | Type | Normal side |
|---|---|---|---|
| 1000 | Cash | asset | debit |
| 1010 | Bank | asset | debit |
| 1100 | Fees Receivable | asset | debit |
| 4000 | Fee Income | income | credit |
| 4010 | Other Income | income | credit |
| 5000 | Discounts and Scholarships | expense | debit |
| 6000 | General Expenses | expense | debit |

| Action | Debit | Credit | Entry date |
|---|---|---|---|
| Post invoices | 1100 | 4000 | **the day you click post** (`date.today()`), not `issued_date` |
| Record payment | 1000 (only if method=cash) / 1010 otherwise | 1100 | `payment_date` |
| Reverse payment | 1100 | 1000/1010 | `date.today()` |
| Apply adjustment/waiver | 5000 | 1100 | `date.today()` |
| Record expense | the chosen expense account | **always 1000 Cash** | `expense_date` |

Posting-date basis means a late-posted invoice bills one term and journals another;
`income_statement()` warns about exactly that ([backend/apps/finance/selectors.py:1053](backend/apps/finance/selectors.py#L1053)).

### 8.2 Set a fee structure ⚙️ (finance role)

`/finance/fee-structures/add/` (+ a `.xlsx` upload job: template is `label, amount` with three sample
rows, [backend/apps/finance/services.py:293](backend/apps/finance/services.py#L293)).
`FeeStructure` is (class, term, **version**), unique together
([backend/apps/finance/models.py:86](backend/apps/finance/models.py#L86)); creating a new one bumps the
version and de-activates the old rows ([backend/apps/finance/services.py:247](backend/apps/finance/services.py#L247)).
Items are `FeeItem` rows (label/amount/order) and `total` is their sum.

⚠️ **Revise before you invoice, not after.** Uniqueness of an invoice is per
(student, term, **fee_structure**), so if you post invoices from v1 and then create v2 and re-run billing,
each pupil gets a **second posted invoice** for the same term and `billed` doubles. Nothing cancels v1's
invoices — `Invoice.Status.CANCELLED` is never written anywhere in the code. If you must fix this, reverse
it by hand in the database or raise it as a defect; do not "just re-post".

### 8.3 Bill a class or the school ⚙️ (finance role)

`/finance/invoices/generate/` — `InvoiceGenerateView`
([backend/apps/finance/views.py:286](backend/apps/finance/views.py#L286)). GET with `?term=` runs a
**dry-run preview** (`preview_invoice_generation`) that labels every pupil `create`, `skip_existing` or
`skip_no_structure` ([backend/apps/finance/services.py:398](backend/apps/finance/services.py#L398)); POST
calls `post_invoices` ([backend/apps/finance/services.py:470](backend/apps/finance/services.py#L470)).

- Scope: pupils with status `active` or `repeating`, in one class or in every class of the term's year.
- Per pupil it uses the **highest-version active** structure for that class+term; none → skipped, reported.
- `get_or_create(student, term, fee_structure)` makes re-running safe — no duplicate journal entry.
- `InvoiceLine`s snapshot each fee item's **label and amount**, so later catalogue edits cannot rewrite
  history ([backend/apps/finance/models.py:216](backend/apps/finance/models.py#L216)).
- Journal: `INV-<invoice_number>`, DR 1100 / CR 4000, dated today.
- Audit `finance.invoices_posted` + feed event `finance.invoices_posted` ("{count} invoice(s) posted for
  {term} ({class})").
- ⚠️ `Invoice.due_date` is **never set** by this service, so the aging report (§8.7) falls back to
  `issued_date`. If you need true due-date aging, due dates must be set manually or by a future change.

### 8.4 Record a payment ⚙️ (finance role)

`/finance/payments/record/` → `PaymentRecordView`
([backend/apps/finance/views.py:352](backend/apps/finance/views.py#L352)),
`PaymentRecordForm` ([backend/apps/finance/forms.py:181](backend/apps/finance/forms.py#L181)):

| Field | Rule |
|---|---|
| Student | dropdown of active/repeating pupils, ordered by surname — not a search box |
| Amount | Decimal(14,2), minimum 0.01 |
| Method | cash / bank / mobile_money / other — **only `cash` debits 1000; the other three go to 1010 Bank** |
| Reference | required, ≤100 chars, **globally unique** (bank ref / MoMo TxID). A duplicate is rejected with a friendly message before the DB constraint |
| Payment date | HTML date input; this is what the cash window counts on |
| Notes | optional |

What `record_payment` then does ([backend/apps/finance/services.py:599](backend/apps/finance/services.py#L599)):

1. Creates the `Payment` (auto `receipt_number` `RCP-…`).
2. Allocates **oldest first** across the pupil's posted invoices, ordered `(issued_date, pk)`, capping each
   allocation at that invoice's outstanding and skipping settled ones.
3. **Overpayment is not allocated** — the leftover is intentionally dropped with a code comment
   ([backend/apps/finance/services.py:664](backend/apps/finance/services.py#L664)) — yet the journal still
   credits the **full** amount to 1100. Consequence: the receivable account goes below the sum of invoice
   debts, and the difference surfaces as `cash_received()["unallocated"]`. Treat an overpayment as a
   credit on the pupil's account, verify it via the statement, and record it in `notes`.
4. Posts `PMT-<receipt_number>` (DR cash/bank, CR 1100) dated `payment_date`.
5. Writes audit `finance.payment_recorded`, feed event `finance.payment_posted`, and invalidates the
   Director KPI cache for the affected term.
6. Receipt: `/finance/payments/<pk>/receipt/` ✅ — that template does exist
   (`features/finance/payment_receipt_pdf.html`: receipt no., date, pupil, admission no., class, method,
   reference, amount, the per-invoice allocation table, recorded-by, and a red "Reversed: reason" banner).
   It still needs a working PDF engine, so on this machine it answers 503.

Payments list `/finance/payments/` hides reversal rows by default.

### 8.5 Reverse a payment ⚙️ (finance role)

`/finance/payments/<pk>/reverse/` → `reverse_payment`
([backend/apps/finance/services.py:732](backend/apps/finance/services.py#L732)). **There is no delete.**
Reason is mandatory, minimum 10 characters ([backend/apps/finance/forms.py:235](backend/apps/finance/forms.py#L235)).

It writes four things, in order: (a) journal `REV-<receipt_number>` dated today, mirroring the original's
asset leg (CR cash/bank, DR 1100); (b) **deletes the original's `PaymentAllocation` rows**, which restores
every invoice's outstanding; (c) flags the original `is_reversed=True` + `reversal_reason`; (d) inserts a
new `Payment` row with `reference="REV-…"`, **negative amount**, `payment_date=today`, `reverses=<original>`.

Afterwards: collected ↓, cash/bank ↓, receivable ↑, pupil balance ↑, Director cache invalidated per
affected term. Audit `finance.payment_reversed` + feed event of the same name. A payment that is already
reversed, or already has a reversal row, raises — reversals are not re-reversible.

⚠️ Because the reversal row is dated **today**, a reversal of a September payment made in October leaves
September's cash window at 200 gross and puts the −200 in October. That is correct cash accounting; just
don't expect a past window to restate itself.

### 8.6 Waivers, discounts, expenses ⚙️ (finance role)

- **Adjustment**: `/finance/adjustments/create/` → `apply_adjustment`
  ([backend/apps/finance/services.py:848](backend/apps/finance/services.py#L848)). Types discount /
  scholarship / waiver / other, amount ≥ 0.01, reason ≥ 5 chars. Posts DR 5000 / CR 1100 dated today.
  It does **not** change `Invoice.total_amount` — so the invoice page still prints its own outstanding
  while term/class/school figures and the statement reduce it.
  ⚠️ An adjustment with **no invoice selected** is invisible to `fee_totals`, `class_fee_rows` and
  `student_fee_rows` (they all filter through invoices) but still moves the pupil's statement balance.
  Always attach the invoice when the waiver is about a specific bill. Audit only — adjustments emit **no
  feed event**, so they never appear in the admin activity feed.
- **Expense**: `/finance/expenses/record/` → `record_expense`
  ([backend/apps/finance/services.py:917](backend/apps/finance/services.py#L917)). Account must be
  `type=expense` and active (the form already filters), amount ≥ 0.01, date, description, optional
  attachment (to `expense_attachments/`) and approval note. DR the account / **CR 1000 Cash — always
  cash, never bank**, which is a real limitation if you pay from the bank. Audit only, no feed event.
  New expense categories: `/finance/accounts/add-expense/` (uppercases the code, forces
  type=expense/normal=debit, `is_system=False`).
- **Manual journal entries: 🚧 no UI.** `SourceType.MANUAL` exists but only the private `_post_journal_entry`
  can create rows, and only tests call it. Balancing rules any manual poster must satisfy: Σdebit = Σcredit
  else `ValueError`, zero-total rejected, one-sided lines and negative amounts blocked by DB constraints.
- **Refunds: 🚧 not modelled.** No refund service or field exists. Today you express a refund as a payment
  reversal (which restores the debt) or as an expense.

### 8.7 Reading the books

| Question | Function | On screen |
|---|---|---|
| Term headline: billed/collected/adjusted/outstanding/rate | `fee_totals` | Director fee page ✅, finance report ⚙️ |
| Per class | `class_fee_rows` (3 GROUP BYs) | Director fee page ✅ |
| Per pupil (debtors) | `student_fee_rows(only_debtors=True)` | Director debtors ✅, finance class page ⚙️ |
| What the total is made of | `fee_item_breakdown` | ⚙️ |
| Overdue buckets | `outstanding_aging` — not_due / 1-30 / 31-60 / 61-90 / over-90, on `due_date` else `issued_date` | ⚙️ |
| Collected by month, cumulative | `collected_by_month` | ⚙️ |
| Cash in a window (gross/reversed/net/count/applied/unallocated) | `cash_received` | Director books ✅ |
| The receipts behind it | `receipts_register` (total = `cash_received().net`, reversal rows included) | ⚙️ |
| Balance per account, as-of a date | `account_balances` | Director books ✅ |
| Cash + bank | `cash_and_bank_balances` — "per the ledger, not per the bank statement" | Director books ✅ |
| Income statement | `income_statement` | Director books ✅ |
| Top expense categories | `top_expense_categories` | ⚙️ |
| Income vs expense by month | `monthly_income_expense` | Director books chart ✅ |
| Trial balance + the balanced verdict | `trial_balance_at(date_to=…)` | Director trial balance ✅, Finance ⚙️ |
| A pupil's account | `student_statement` / `student_balance` | Finance ⚙️ (and the PDF template is missing) |

**The finance screens themselves are ⚙️ today.** All 26 of their templates are absent — every
`features/finance/*.html` referenced by views except `payment_receipt_pdf.html`
(verified: 100 templates referenced, 26 missing, all in `features/finance/`). Opening `/finance/`,
`/finance/ledger/`, `/finance/payments/`, `/finance/reports/outstanding/`, a pupil statement or the
student "My fee statement" nav item raises `TemplateDoesNotExist` (a 500 page in dev) even though the role
gate passes. This is the known deferred work (Admin checkpoints 6–7), and
[backend/apps/core/tests/test_navigation_integrity.py:51](backend/apps/core/tests/test_navigation_integrity.py#L51)
exempts those URLs so the suite stays honest about it. **The Director portal is the way to read the books
until then** — it renders from the same interface and its templates all exist.

Exports that do work ✅ from the Director side: `/dashboards/director/export/performance/`,
`/export/fees/`, `/export/staff/`, `/export/books-pdf/`. The finance outstanding-fees .xlsx
(`/finance/reports/outstanding/export/`) is wired and returns a file — it needs a valid `?term=` or it
redirects back — with columns Admission No. / Student Name / Class / Term / Total Billed / Collected /
Waived / Adjusted / Outstanding / Last Payment
([backend/apps/finance/services.py:1065](backend/apps/finance/services.py#L1065)).

Fee reminders: `/finance/reminders/` queues `send_fee_reminder_task`. ⚠️ **The task ignores
`student_ids`** and announces to *every* student and parent — it only logs the ids
([backend/apps/finance/tasks.py:64](backend/apps/finance/tasks.py#L64)). Do not press it expecting a
targeted reminder.

---

## 9. Results

### 9.1 Grading setup ✅ (admin)

Grading is **exam-only**: every mark is a single raw score out of 100. There is no
continuous-assessment weighting.

`/results/grading/` (list) → `/results/grading/<year_id>/` sets the year's pass mark
(`save_grading_config`, [backend/apps/results/services.py:62](backend/apps/results/services.py#L62)).
Each year has **at most one grading system** (`GradeScale`, enforced by a OneToOne on
`academic_year`) edited at `/results/grading/<year_id>/scale/`
(`save_grade_scale`, [backend/apps/results/services.py:215](backend/apps/results/services.py#L215)).
A system holds its grade bands (`min_score`/`max_score` inclusive, grade, remark, points)
and a **class checklist**: ticked classes are graded by the system, unticked classes fall
back to the year's legacy `GradeBand` rows.

Band validation (pinned in
[backend/apps/results/tests/test_grade_scales.py](backend/apps/results/tests/test_grade_scales.py)):
bands must **start at 0**, **end at 100**, not overlap and not gap (adjacency allowed only
when `next.min == prev.max + 1`), and grade labels must be unique within a system. Every
class in the checklist must belong to the system's academic year.

Saving a system **deletes and recreates** its bands, updates the class selection and
immediately **re-grades every scored entry for every class in that year** (published ones
included), then writes `results.grade_scale_saved` to the audit log. Removing the system
(`delete_grade_scale`) clears the class assignments and re-grades against the year bands.

### 9.2 Sheet lifecycle — the governance state machine

`ResultSheet` is one class + subject + term (+ optional stream), unique
([backend/apps/results/models.py:166](backend/apps/results/models.py#L166)), and moves through
`draft → submitted → confirmed → published` with these legal transitions
([backend/apps/results/models.py:100](backend/apps/results/models.py#L100)):

| From | To | Screen | Rule |
|---|---|---|---|
| draft | submitted | 🚧 none | `submit_sheet` exists in the service but **no view calls it** |
| submitted | confirmed | `/results/sheets/<pk>/confirm/` ✅ admin | |
| confirmed | published | `/results/sheets/<pk>/confirm/` flow + `/results/publish/class-term/` ✅ admin | publish invalidates the Director cache and emits the `results.published` feed event |
| any | draft | `/results/sheets/<pk>/reopen/` ✅ admin | **reason required, non-blank**, stored on the sheet and in the audit summary; clears submitted/confirmed/published stamps |
| draft | confirmed/published | — | rejected: `ValidationError("Cannot transition from 'draft' to …")` |
| published | submitted | — | rejected; un-publish goes through draft (reopen) only |

`is_locked` = status in {confirmed, published}
([backend/apps/results/models.py:185](backend/apps/results/models.py#L185)) and a locked sheet refuses score
edits. There is **no deadline logic** — `Term.end_date` is never consulted — and no "rejected" state or
per-entry comment.

Every transition writes exactly one `AuditLog` row (`results.submitted` / `results.confirmed` /
`results.published` / `results.reopened`) with `{from, to}` in the summary
([backend/apps/results/services.py:224](backend/apps/results/services.py#L224)).

### 9.3 Entering marks ✅ (teacher portal)

Teachers enter exam marks at `/teachers/marks/` → `/teachers/marks/<assignment_id>/`
([backend/apps/teachers/views.py](backend/apps/teachers/views.py)). The list shows only the
signed-in teacher's current-year `TeacherAssignment` rows; the entry page 404s for any
assignment that is not theirs. The page picks a term + exam (Exam 1–10 are created lazily by
`ensure_exams_for_term`), lists the class roster (stream-filtered when the assignment names a
stream) and saves one mark out of 100 per pupil.

Marks are saved through `upsert_score_entry`
([backend/apps/results/services.py](backend/apps/results/services.py)): blank clears the pupil's
entry, `0 ≤ mark ≤ 100` is enforced, the mark is stored as both `raw_score` and `total` (so the
student-facing pages keep working) and graded by the class's scale (or the year fallback).
Marks land on a **draft** sheet; "Submit to Admin" calls `submit_sheet`. Once submitted the
teacher can no longer edit — only an admin reopen returns it to draft. Confirmed and published
sheets are locked.

Admins review the submitted sheets and entries at `/results/sheets/` and
`/results/sheets/<pk>/`; the class view is `/results/broadsheet/`. `ResultSheet`,
`ScoreEntry`, `GradingConfig`, `GradeScale` and `GradeBand` are not registered in Django
admin ([backend/apps/results/admin.py](backend/apps/results/admin.py) is a stub).

The service path is still available from `python manage.py shell`:

```python
from apps.results.services import create_result_sheet, upsert_score_entry, submit_sheet, \
    confirm_sheet, publish_sheet
sheet = create_result_sheet(school_class=cls, subject=subj, term=term, actor=admin)  # DRAFT
upsert_score_entry(sheet=sheet, student=pupil, raw_score="75", actor=admin)
submit_sheet(sheet=sheet, actor=admin); confirm_sheet(sheet=sheet, actor=admin)
publish_sheet(sheet=sheet, actor=admin)        # or publish_class_term(class, term, actor)
```

### 9.4 Reading results ✅

- Pupil/parent: `/results/my-results/` — term dropdown (default = most recent term with published
  results), per-subject total/grade/remark/position, summary cards (average %, class position n/m, subject
  count). Only `status=published` sheets are ever visible
  ([backend/apps/results/selectors.py:96](backend/apps/results/selectors.py#L96)).
- Report card PDF: `/results/report-card/<student_id>/<term_id>/pdf/` — header with school name/motto,
  pupil/class/stream/term meta, **Overall Average %** and **Subjects Taken** boxes, and a
  Subject / Total / Grade / Remark / Position n/m table with a "published results only" note. 404 when the
  pupil has no published entries for that term; needs the PDF engine.
- Position formulas: class position = 1 + number of classmates whose average of published totals is
  strictly higher ([backend/apps/results/selectors.py:177](backend/apps/results/selectors.py#L177));
  subject position = 1 + count of entries on the same sheet with a higher total
  ([backend/apps/results/selectors.py:203](backend/apps/results/selectors.py#L203)). Ties share a position
  (no competition ranking). These are computed at read time, and `class_performance` is not cached, so a
  big school pays per request (see §17 for the budgets).
- `SchoolSettings.show_positions` exists but the PDF and page do not consult it — positions always render.

---

## 10. Homework

Read ✅ / write 🚧. `Homework` has title, description, subject, class, optional stream, term, `set_by`
(roles limited to teacher), `due_date`, optional attachment (`upload_to="homework/"`), and
`status = draft|published` ([backend/apps/homework/models.py:14](backend/apps/homework/models.py#L14)).

Pupils and parents: `/homework/` with `?term=` and `?subject=` filters, an overdue flag computed in
Python, and `/homework/<pk>/download/` for attachments. Visibility rule
([backend/apps/homework/selectors.py:16](backend/apps/homework/selectors.py#L16)): published only, own class
only; **if the pupil has a stream**, they see items with that stream *or* stream-blank ("all streams");
**if they have no stream**, they see stream-blank items only. Downloads re-check published + class +
stream (403) and 404 when there is no file.

There is **no create/edit/publish/delete screen** ([backend/apps/homework/urls.py](backend/apps/homework/urls.py)
has list + download only), no submissions, no grading, no marks, no due-date reminders, and no attachment
size/type validation. Homework rows can only be created via the shell or the demo seeder.

---

## 11. Attendance — ⚙️ everything, no screen

Two tables ([backend/apps/attendance/models.py](backend/apps/attendance/models.py)):
`AttendanceRegister` = one class on one day, unique `(school_class, date)`, `clean()` rejects future dates,
`recorded_at` stamped on first fill and never moved; `AttendanceEntry` = one pupil's mark, unique
`(register, student)`, `status ∈ present|absent|late|excused`, `reason`, `date` copied from the register,
student FK `PROTECT` so a pupil with marks cannot be deleted.

**Rate convention:** `ATTENDED_STATUSES = (present, late)` — excused is *not* in the numerator — and the
denominator is every entry in the window ([backend/apps/attendance/selectors.py:8](backend/apps/attendance/selectors.py#L8)).
A 100% attendance rate therefore means "nobody was absent", not "nobody was unaccounted for"; `excused`
pupils do count against the rate. `summarise()` returns `rate=None` when there is no data rather than 0.

Marking: `record_register(school_class, date, marks, actor)`
([backend/apps/attendance/services.py:28](backend/apps/attendance/services.py#L28)) — idempotent
`get_or_create` on (class, date), last line per pupil wins, changed rows updated in place, then one feed
event + one audit row `attendance.register_recorded` with the present/absent/excused/late counts.
Selectors: `summarise`, `by_class`, `by_weekday` (always all 7 days), `chronic_absences(threshold=5,
absent-only)`, `marking_compliance` (expected vs filled registers), `register_for`, `student_history`.

**UI: none.** `/attendance/` maps to a `ComingSoonView`
([backend/apps/attendance/views.py:6](backend/apps/attendance/views.py#L6)), there is no nav entry for any
role, nothing outside the app consumes its selectors, no admin registration, and recording a register
invalidates no cache. The Teacher portal (Module 2) is the checkpoint that gives it a screen.

---

## 12. Communications

- **Announcements** `/communications/` ✅ for every role (read). Create at
  `/communications/announcements/add/` ✅ admin **and director** — the Director's one permitted write
  ([backend/apps/dashboards/views_director.py:55](backend/apps/dashboards/views_director.py#L55)).
  Audiences include everyone / by role / by class; fan-out creates notifications capped at **500
  recipients**, `bulk_create(ignore_conflicts=True)` inside try/except that logs
  `announcement_notification_failed` ([backend/apps/communications/services.py:161](backend/apps/communications/services.py#L161)) —
  a dead notification backend never fails the announcement (proven in
  [backend/tests/e2e/test_e2e_flows.py:403](backend/tests/e2e/test_e2e_flows.py#L403)).
  `read-all/` and per-announcement `read/` mark state; "No announcements yet … Phase 5" text still sits on
  the teacher and finance homes.
- **Message threads / complaints** — pupils and parents raise a complaint (`messages/new/`) and see their
  own (`messages/`); staff open a thread from `/communications/inbox/<pk>/` — note the inbox itself is
  admin+director only, while a single thread also allows teacher/finance
  ([backend/apps/communications/urls.py](backend/apps/communications/urls.py)), so those two roles can open a
  link but cannot navigate to the list. Replies create notifications
  ([backend/apps/communications/services.py:52](backend/apps/communications/services.py#L52)).
  There is no `resolved_at`, so resolution time cannot be reported.

---

## 13. The Director portal ✅ (read-only analysis)

Every URL uses `DirectorRequiredMixin` — **admin cannot use Director URLs and vice versa** — and every one
is a read. Admin gets 403 here, teacher/finance/student/parent too, and POST to any mutating URL is
403/302/405 ([backend/apps/dashboards/tests/test_director_access.py:87](backend/apps/dashboards/tests/test_director_access.py#L87),
[:139](backend/apps/dashboards/tests/test_director_access.py#L139)). Note one weak assertion there:
the results POST check at `:153` tolerates 200, so it proves nothing.

| Screen | URL | What it shows | Where the numbers come from |
|---|---|---|---|
| School at a glance | `/dashboards/director/` | KPI cards (pupils, gender split, class counts, staff, billed/collected/adjusted/outstanding, pass rate, subject average), enrollment + fee charts, quick links | `get_cached_school_kpis` → `school_kpis` ([backend/apps/dashboards/selectors.py:40](backend/apps/dashboards/selectors.py#L40)); money via `finance.fee_totals` |
| Class performance | `/dashboards/director/performance/` | per class: pupils, avg score, pass rate, per-subject rows | `class_performance` (uncached) |
| Class drill-down | `/dashboards/director/performance/<class_id>/` | that class's detail + trend | `class_performance`, `class_performance_trend` |
| Fee analysis | `/dashboards/director/fees/` | per class billed / collected / waived / outstanding / rate + chart | `class_fee_summary` = thin call to `finance.class_fee_rows` |
| Debtors | `/dashboards/director/fees/<class_id>/debtors/` | pupils still owing, largest first | `finance.student_fee_rows(only_debtors=True)` |
| Staff directory | `/dashboards/director/staff/` | every non-student/parent user, searchable by name/role | `staff_directory` |
| Books of account | `/dashboards/director/books/` | income vs expense by month, cash/bank, summary | `finance.report_summary`, `monthly_income_expense` |
| Ledger | `/dashboards/director/books/ledger/` | journal lines, account/date filters, **capped at 500 rows** | `finance.ledger_entries` |
| Trial balance | `/dashboards/director/books/trial-balance/` | every account's debit/credit and Finance's own "does it balance" tick | `finance.trial_balance_at()` — the verdict is Finance's answer, not re-added in the view |
| Exports | `/dashboards/director/export/{performance,fees,staff}/`, `/export/books-pdf/` | .xlsx (4 files) | `core.exports` / `core.pdf`; `400 No current term.` when nothing is flagged |

Charts are **local Chart.js 4.4.4** ([frontend/static/vendor/chart.js/4.4.4/chart.umd.min.js](frontend/static/vendor/chart.js/4.4.4/chart.umd.min.js)),
loaded per page via `{% block extra_js %}`; [backend/tests/test_static_assets.py](backend/tests/test_static_assets.py)
fails the build if a CDN reference or a missing `{% static %}` path appears.

Two guarantees worth knowing when you read a Director page: the fee page and the Finance page for the same
term are **the same rows** (asserted at selector *and* rendered-page level in
[backend/apps/dashboards/tests/test_director_finance_reconciliation.py](backend/apps/dashboards/tests/test_director_finance_reconciliation.py)),
and the KPI cards are the interface's numbers, so a waiver or a reversal moves both sides together.

---

## 14. Audit, events and notifications — three separate paths

| Path | What it is | Used for | Where you see it |
|---|---|---|---|
| `core.audit_log.log_action` | `AuditLog` row: actor, action, object, before/after summary, IP (X-Forwarded-For else REMOTE_ADDR), request id | "what changed on this object" | `/audit/log/` ✅ admin, filterable, 50/page; also on the admin home feed |
| `core.events.record_event` | `SystemEvent` row + a structured log record, written in a savepoint so a failed feed row can never roll back the business write, and **never raises** | the human "what happened at school today" feed | `/activity/feed/` ✅ admin, grouped by day |
| `core.business_events.log_event` | JSON log line to the `apps.events` logger | machine-readable trail | stdout / log file (JSON in prod) |

Wording comes from the `EVENT_TYPES` registry ([backend/apps/core/events.py:61](backend/apps/core/events.py:61));
registered: `attendance.register_recorded`, `finance.invoices_posted`, `finance.payment_posted`,
`finance.payment_reversed`, `results.published`. Unregistered types fall into category "other", and
`render_message` fills missing keys with an em dash and clips to 255 chars.

**Asymmetry worth knowing:** only invoice posting, payment recording and reversal reach the activity feed.
Adjustments, expenses, fee-structure creation, student enrolment and note edits are **audit-log only** — they
will never appear at `/activity/`. `/activity/daily/` ✅ admin summarises a day (category counts, money
collected pulled from the event JSON, 100-row cap).

`Notification` rows (`core/notifications.py notify/notify_many`) are mainly created by communications.

---

## 15. School settings ✅ (admin)

`/school/` edits the singleton `SchoolSettings` pk=1 ([backend/apps/core/models.py:135](backend/apps/core/models.py#L135)):
school name, motto, phone, email, currency, `show_positions`, `fees_enabled`. `load()` is
`get_or_create`, and `school_settings_values()` merges the row over `SCHOOL_*` env defaults
([backend/apps/core/selectors.py:142](backend/apps/core/selectors.py#L142)) — so the env file is the
fallback, the row is the override, and a missing row still renders sensible values. Every template gets
them through `context_processors.school_settings`. Writes go through
`core.services.update_school_settings` and are audit-logged.

---

## 16. Files: exports and PDFs, and why they behave like that

[backend/apps/core/exports.py](backend/apps/core/exports.py) is the only sanctioned way to produce a
spreadsheet:

- `start_workbook(title)` — so no view imports openpyxl (the Director guard forbids it).
- `append_rows(ws, rows)` — **formula-injection defence.** openpyxl writes a string beginning `=` as a live
  formula, and Excel evaluates it on open. Any cell whose text starts with `= + - @ TAB CR` is forced to
  `data_type = "s"`, stored as literal text, value unchanged and byte-identical on round-trip
  ([backend/apps/core/exports.py:36](backend/apps/core/exports.py#L36)). This matters because exports mix in
  text somebody else typed — a bulk-import name or an error message could otherwise become a command.
- `format_column_widths(ws)` — auto-size, capped at 40.
- `stamp_document(wb, title, source, actor)` — writes creator `School Management System`,
  `lastModifiedBy = <username>`, the title, and `"source — exported by user on <timestamp>"` into the file
  properties, so an attachment can be traced to the screen and person that produced it.
- `xlsx_response(wb, filename)` — attachment headers.

[backend/apps/core/pdf.py](backend/apps/core/pdf.py) `pdf_response(html, filename, base_url, inline=True)`:
a real PDF, or a **503 plain-text** answer with the reason logged (`pdf_engine_unavailable` /
`pdf_render_failed`). It never returns HTML under a `.pdf` filename — the old behaviour silently produced a
"PDF" that was a web page. `apps/core/tests/test_pdf.py` and the stubs in the access tests pin both paths
without depending on the host's native libraries.

---

## 17. Caching and performance

One cache key today: `director_kpis_term_{term.pk}`, TTL `DIRECTOR_KPI_CACHE_TTL` default 600s, and the
stored dict carries `cached_at` which the home page prints as "Updated …"
([backend/apps/dashboards/cache.py:24](backend/apps/dashboards/cache.py#L24)).

Invalidated by: `finance.record_payment` (with a term-from-invoice fallback),
`finance.reverse_payment` (per affected term), `results.publish_sheet`. All three wrapped in silent
try/except.

**Not invalidated by**: `post_invoices`, `apply_adjustment` (so a waiver can show as debt for up to 10
minutes), `record_expense`, fee-structure changes, and all of attendance. Only the TTL fixes those — if a
Director number looks stale after one of those actions, that is why. Note also that
`DIRECTOR_KPI_CACHE_TTL` is not in the env-var list in `base.py`, so `docs/performance.md`'s advice to set
it in `.env` only works via the code default being read at call time.

Query budgets enforced by [backend/apps/dashboards/tests/test_director_query_count.py](backend/apps/dashboards/tests/test_director_query_count.py):
`school_kpis` ≤ 12 (11 measured), `class_fee_summary` ≤ 5, Director home cold ≤ 20, warm ≤ 12. The
interface is written so those stay flat as the school grows: `fee_totals` = 3 queries, `class_fee_rows` = 3
GROUP BYs, `student_fee_rows` = 1, `outstanding_aging` = 1, `trial_balance_at` = 2. `class_performance` and
the position formulas are the known N+1 offenders (D4 territory).

`docs/performance.md` holds p50/p95 tables and thresholds — explicitly labelled indicative, not measured on
this hardware.

---

## 18. Data safety, seeding, backup

- **Seeder** ✅ — the repo's only custom command:
  `python manage.py seed_demo_data` ([backend/apps/core/management/commands/seed_demo_data.py](backend/apps/core/management/commands/seed_demo_data.py)),
  wrapped by [scripts/seed_demo_data.py](scripts/seed_demo_data.py). Builds "Greenfield Academy": 1 year,
  3 terms, 6 classes, pupils, teachers + assignments, fee structures, posted invoices, payments, expenses,
  published results, homework, announcements, threads, notifications. Writes go **through the services**, so
  the audit and ledger trails look like real usage. Idempotent by natural keys, fixed RNG. Safety: refuses
  to run under prod settings unless `--force`, and `--dry-run` rolls everything back via a forced exception.
  Demo credentials are listed at the top of the file (lines 75–92). It seeds **no attendance**.
- **Backup**: [scripts/backup_db.sh](scripts/backup_db.sh) — `pg_dump -Fc` or SQLite `.backup`, `KEEP_DAYS=30`.
  **Restore**: [scripts/restore_db.sh](scripts/restore_db.sh) — destructive, drops the target; the documented
  stop/restore/migrate/`/readyz/` procedure is in that file.
- Migrations policy in this repo: **add new migrations only, never edit or delete applied ones**.
  `finance` migration 0003 added seven indexes and 0004 removed them again — that is the correct pattern
  (a new migration reversing the last), not an edit.
- The dev database is `backend/db.sqlite3`. Tests never touch it — they build a throwaway in-memory SQLite
  through real `migrate`.

---

## 19. What the 459 tests protect

```
cd backend && python -m pytest -q
```

| Area | Files | Invariant |
|---|---|---|
| Ledger | `finance/tests/test_ledger.py` (15) | append-only (save/delete raise), balanced entries enforced, negative amounts blocked by DB, duplicate payment reference cannot double-post, reversal restores invoice |
| Money interface | `finance/tests/test_money_interface.py` (45) | every definition in §8.1 against plain arithmetic and against the ledger, query budgets, the two-bases gap |
| Director ↔ Finance | `dashboards/tests/test_director_finance_reconciliation.py` (13), `test_director_selectors.py` (30) | Director selectors and **rendered page contexts** equal Finance's answers; rows sum to totals; waiver is not debt; reversal moves both sides |
| Contract guard | `dashboards/tests/test_director_finance_contract.py` (9) | Director code imports no finance models/services, does no money arithmetic, imports no spreadsheet/PDF library |
| Director exports | `dashboards/tests/test_director_exports.py` (7) | files route through the shared helpers, formula-shaped text stays text, provenance stamped, PDF fails loudly |
| Export/PDF helpers | `core/tests/test_exports.py` (14), `test_pdf.py` (7) | injection rule, width/stamp behaviour, 503-not-HTML rule |
| Access | `core/tests/test_security_matrix.py` (9), `dashboards/tests/test_director_access.py` (20), `tests/e2e/test_role_matrix.py` | URL × role matrix, superuser-with-a-role is denied, unauthenticated redirects |
| Scope / IDOR | `results/tests/test_access_and_grades.py` (7), `dashboards/tests/test_parent_scope.py` (3), `homework/tests/test_scope.py` (2) | query params cannot change identity, published-only, class/stream isolation |
| Governance | `results/tests/test_governance.py` (16) | grading validation, the transition table, reopen-requires-reason, class-term publish |
| Performance | `dashboards/tests/test_director_query_count.py` (5), `core/tests/test_navigation_integrity.py` (5), `tests/test_static_assets.py` (5) | query budgets, every nav entry reaches a real view/template, no CDN assets |
| Platform | `core/tests/{test_events,test_activity_pages,test_school_settings,test_features,test_audit_log,test_health,test_seed_demo_data}.py` | event registry tolerance, feed/report pages, settings singleton, feature registry, seeder safety |
| End-to-end | `tests/e2e/test_e2e_flows.py` (5) | enrol → bill → pay → statement → receipt → reverse; publish → cache invalidation; core flows survive notification and PDF failure |
| Credentials | `accounts/tests/test_staff_management.py` (19), `students/tests/test_students.py` (+4) | the admin's password is stored verbatim, confirmation mismatch and weak passwords rejected even with validators off in dev, the force-change box is honoured, blank student password still means admission number, the email prints username and password on separate lines |

`KNOWN_BROKEN` in [backend/apps/core/tests/test_navigation_integrity.py:49](backend/apps/core/tests/test_navigation_integrity.py#L49)
is a **shrink-only** list: it records the missing finance templates so the rest of the suite stays green, and
the test fails if the list grows.

---

## 20. What is not there yet (ranked by operational impact)

1. **Finance screens (26 templates)** — money is fully calculated, guarded and tested, but no cashier can
   see or touch it in the browser; every finance URL 500s on `TemplateDoesNotExist`. This is Admin
   checkpoint 6–7, deliberately deferred. Until then: Director pages read the books, and writes happen via
   the service layer.
2. **Results entry has no UI and no admin registration** — sheets and scores can only be created from the
   shell or the seeder; teachers cannot reach any results screen; `submit_sheet` is unreachable. That is
   Module 2 (Teacher portal) work.
3. **Attendance is service-only** — model, constraints, marking service and selectors are done and tested;
   `/attendance/` is a "coming soon" page, no role can mark, and no dashboard consumes it.
4. **Homework has no authoring screen** — pupils read it, nobody can write it.
5. **No student edit / withdraw / restore / delete screen.** `status` changes only via promotion.
6. **No refunds, no manual journal UI, no bank-paid expense**, no invoice cancellation flow,
   invoice-less adjustments invisible to the fee totals, and no `due_date` set at invoicing so aging falls
   back to issue date.
7. **Fee reminders broadcast to all students and parents** regardless of which pupils you selected.
8. **`fees_enabled` feature flag is inert** — stored, editable, tested, consumed by nothing.
9. **Teacher and Finance home pages are placeholders** — hard-coded "–" stat cards and "Phase 5" text even
   though the views pass real context ([frontend/templates/dashboards/teacher/home.html:19](frontend/templates/dashboards/teacher/home.html#L19)).
10. **Director KPI cache is not invalidated** by invoice posting, waivers, expenses or attendance (§17).
11. **Queued jobs need `qcluster`** — credential emails currently sit unsent (3 in this repo's dev DB).
12. **Deferred by explicit decision**: Director balance sheet, scheduled digests, presentation mode,
    signed expiring attachment links, and the `student_statement` all-time-vs-term defect (D7); Module 3
    (student portal S1–S12) and Module 2 (teacher portal T1–T11) are still queued behind the Director series.

---

## 21. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 500 `TemplateDoesNotExist: features/finance/…` | finance UI not built | Use the Director portal to read money; the screen is deferred work |
| Download answers **503 "No PDF was produced…"** | WeasyPrint's native libs missing (`gobject-2.0-0` on Windows) | Install GTK/WeasyPrint deps, or use the .xlsx exports, which need no native library |
| Director pages show "No current term" / export answers 400 | no `Term`/`AcademicYear` flagged `is_current` | `/academics/years/` → Set current; then set the current term |
| A Director number looks stale | TTL-only invalidation | `/dashboards/director/` after 10 min, or `python manage.py shell`: `from django.core.cache import cache; cache.clear()` |
| Waived money still showing as debt on an invoice page | adjustments don't change `Invoice.total_amount`; and invoice-less adjustments don't reach `fee_totals` | Attach the invoice to the adjustment; read term/class figures, not the invoice row |
| Class totals doubled after re-billing | fee structure revised after posting → a second posted invoice per pupil (§8.2) | Don't re-post; correct deliberately and record why |
| Login says locked out with a JSON body | 5 failed attempts, 1-hour axes cooloff | Wait, or an admin can clear `axes_accessattempt` |
| Staff cannot reset their own password | by design (`STAFF_ROLES` blocked) | Admin → Staff users → Reset password |
| New parent/student never gets a credential email | `qcluster` not running; `async_task` silently queues | Run `python manage.py qcluster`, or read the password off the success message and force a change |
| Teacher/finance user gets 403 on Director pages | correct — Director URLs are director-only | Ask for the right role account |
| Superuser gets 403 everywhere admin-only | the superuser account was given a `role` | Clear its role, or use a proper admin account |
| Weak password accepted in dev, rejected in prod | dev disables password validators | Test policy under `config.settings.prod`/`test` |

---

## 22. Glossary

- **billed / collected / adjusted / outstanding** — see §8.1; outstanding never includes waived money.
- **Allocation basis vs receipt-date basis** — the two ways "how much have we collected" is answered;
  never compare them without naming which is which.
- **Reversal row** — a `Payment` with a negative amount created by `reverse_payment`, dated today,
  `reverses` the original; the original keeps `is_reversed=True`. Both must be summed to net correctly.
- **Register / entry** (attendance) — one class-day, and one pupil's mark within it.
- **Sheet** (results) — one class + subject + term (+ stream); the unit that is governed.
- **Selector / service** — read vs write; views may only call these.
- **SystemEvent vs AuditLog vs business event** — feed line vs "what changed on this object" vs JSON log.
- **⚙️ / ✅ / 🚧** — code-only, usable in browser, absent.
