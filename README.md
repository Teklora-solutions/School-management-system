# School Management System

A single-school management platform: admissions, academics, results, finance, homework,
communications, role-based dashboards and the school's own public website.

Built as a **server-rendered Django 5.1 monolith** with a vendored Bootstrap 5 front end.
No Docker, no JS build step, no managed services required — it runs on one small Linux box.

- **Backend:** `backend/` — Django project (`config/`) + 12 local apps (`backend/apps/`) → [backend/README.md](backend/README.md)
- **Frontend:** `frontend/` — Django templates + static assets (no SPA) → [frontend/README.md](frontend/README.md)
- **Deeper reference:** [docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) (architecture handbook),
  [docs/performance.md](docs/performance.md), [docs/decisions/observability.md](docs/decisions/observability.md)

---

## 1. What the system does

| Module | What you can do in it |
|---|---|
| **Admissions & students** | Student records with unique admission numbers, status (active / repeating / graduated / withdrawn), guardians linked to parent accounts, primary-guardian flag, sponsor + payment-plan notes. Bulk import from `.xlsx` with a two-stage preview → validate → commit job. Admission-form and blank-form PDFs. |
| **Academics** | Academic years, three terms per year (with a enforced "current" term), classes ordered by `level_order` for promotion, streams (e.g. Blue/Gold), subject catalogue, and class↔subject maps per class or per stream. |
| **Teachers** | Teacher profiles (staff ID, qualification, department), per-year assignment of teacher ↔ class ↔ subject with a database-enforced **one teacher per class-subject per year**, an assignment matrix, subject-distribution and workload views, and class-teacher appointment. Teachers enter marks against their own assignments only. |
| **Results** | Exam-only marks (one raw score out of 100 per student per subject — no continuous assessment weighting). `ResultSheet` per class+subject+term(+stream) driven by a state machine **draft → submitted → confirmed → published**, with locked sheets, reopen-with-reason, bulk per-class publish, and a full audit row per transition. Grading is a `GradeScale` per academic year (bands must cover 0–100 with no gap or overlap) with a legacy per-year band fallback. Report cards, class broadsheets and student result pages are WeasyPrint PDFs carrying the school logo, motto, class position, class-teacher remark and signature block. Only **published** sheets are visible to students and parents. |
| **Finance** | A real double-entry ledger: chart of accounts (asset / liability / equity / income / expense with a normal side and contra flags), fee structures per class+term with itemised fees, optional items with per-pupil elections, discount/scholarship/waiver rules (including siblings-only), invoices with immutable line snapshots, receipts for bank/cheque/mobile-money, allocations, adjustments, expenses, budgets with variance, vendors, open/closed financial periods, backdating limits, and a second-signature approval queue. Gap-free document numbers from a compare-and-set `NumberSeries`. Trial balance, class and school summaries, outstanding reports, Excel exports, student statements, receipt PDFs with DUPLICATE stamping on reprint, and fee reminders via background jobs. |
| **Homework** | Teachers author homework per class/subject/term/stream with up to 5 attachments, save as draft, publish or unpublish, preview. Students and parents see a list with a NEW badge, submit with up to 5 files, resubmit freely until the due date, and are flagged `is_late` permanently after it. Attachments are stored under UUID names and streamed only through permission-checked views — never exposed as media URLs. |
| **Attendance** | Data model is complete and honest (one register per class per day, present / absent / late / excused, `ATTENDED_STATUSES = (present, late)` so an excused absence counts against the rate, future dates refused, services + selectors written). **The UI is still a "coming soon" stub** — see [Known gaps](#9-known-gaps-and-limitations). |
| **Communications** | Announcements targeted at everyone, at selected roles, or at specific classes, with optional `publish_at` scheduling and expiry, attachments and capped notification fan-out (500 recipients). Read tracking drives the navbar unread badge. A threaded complaints / inquiries / satisfaction desk with `open → in_progress → resolved`, admin/director assignment, replies and attachments. |
| **Dashboards** | Six role homes, each behind its own role mixin: Admin (school-wide governance), Director (a full analytics suite — class performance and drilldowns, class fee overview and debtors, staff directory, "Books of Account", Chart.js visuals, Excel and PDF exports), Teacher (my classes, marks entry, homework), Student (results, fees, homework, announcements), Parent (children switcher, same views per child), Finance (the money interface). |
| **Public website** | The school's own open site served at `/` — home, about, programmes, gallery, contact/enquiry form — built from data the school already maintains plus two small tables (`GalleryPhoto`, `ContactMessage`). Admins edit it under `/website/`, and enquiries are moderated there. |
| **Administration** | A staff directory with create / detail / deactivate / reactivate and admin-only password reset; a single School Settings screen (identity, currency, logo, address, positions on/off, fees display switch); an audit log viewer; an activity feed and daily report; and structured logging with request IDs throughout. |

---

## 2. Tech stack

### Backend
| Concern | Choice |
|---|---|
| Language | Python 3.12+ (the repository venv runs **3.13.7**; `ruff.toml` targets `py313`) |
| Web framework | Django `>=5.1,<5.2` |
| Database | SQLite for development; PostgreSQL 14+ required in production (`psycopg2-binary`) |
| Data validation / env | `django-environ` (`.env` file, silently skipped when real env vars are injected) |
| Background jobs | `django-q2` with the ORM broker — one extra process (`manage.py qcluster`), no Redis needed |
| Login throttling | `django-axes` |
| PDF rendering | `WeasyPrint >=62,<63` |
| Spreadsheets | `openpyxl` (import/export), `pandas` (analysis), `python-dateutil` |
| Images | `Pillow` (logos, avatars, attachments) |
| Static files | `whitenoise` with `CompressedManifestStaticFilesStorage` |
| Logging | `python-json-logger` (JSON in prod, verbose in dev) |
| Error tracking | `sentry-sdk[django]`, activated only when `SENTRY_DSN` is set |
| App server | `gunicorn` (WSGI) |
| Testing | `pytest`, `pytest-django`, `pytest-cov`, `factory-boy`, `nplusone` |
| Lint / format | `ruff`, `black` (both declared; `ruff` is what the guide tells you to run) |
| Load testing | `locust` |
| Dependency audit | `pip-audit` |

`djangorestframework` is installed and configured (session auth, `IsAuthenticated`, page-number
pagination of 25) but **no API endpoints exist yet** — every screen is an HTML view. Treat DRF as a
reserved dependency, not a feature.

### Frontend
| Concern | Choice |
|---|---|
| Rendering | Django templates, server-rendered. **No React/Vue, no bundler, no npm, no build step.** |
| CSS framework | Bootstrap **5.3.3**, vendored locally under `frontend/static/vendor/` |
| Icons | Bootstrap Icons **1.11.3** (CSS + woff/woff2 vendored) |
| Charts | Chart.js **4.4.4** (UMD bundle, vendored, loaded per dashboard page) |
| Custom CSS | ~1,700 lines of hand-written design-system CSS (`base.css` app shell, `public_new.css` website) |
| JavaScript | 84 lines of vanilla JS in `static/js/base.js`, plus per-page inline scripts |
| Fonts | Google Fonts (Inter + Plus Jakarta Sans) — the one external request, and a deliberate exception |

### Offline-first rule (enforced by tests)
Every vendor asset is committed so the app works on a school LAN with no internet.
`backend/tests/test_static_assets.py` fails the suite if any template references an
`http(s)://` script or stylesheet, if a `{% static %}` path does not resolve, if a vendored CSS
points at a missing font, or if a vendor file declares a `.map` that was never shipped. The
allow-list is exactly one entry — `fonts.googleapis.com` — and it is shrink-only.

> The source-map comments were **stripped** from `bootstrap.min.css`, `bootstrap.bundle.min.js`
> and `chart.umd.min.js` so `collectstatic` doesn't abort with `MissingFileError`. If you
> re-download those files from a CDN you will break the build. Add the `.map` files or strip the
> comment again.

---

## 3. Requirements

- Python **3.12 or newer**
- Git
- SQLite (bundled) for development — nothing to install
- **Windows only, for PDFs:** WeasyPrint needs the native Pango/GTK libraries. Without them,
  `import weasyprint` raises `OSError: could not find any/library ... gobject-2.0-0`. The app
  still runs; every PDF route answers an honest plain-text **503** instead of HTML wearing a
  `.pdf` name. Install GTK runtime (e.g. `pacman -S mingw-w64-x86_64-gtk3`, or the official
  GTK3 runtime installer) if you need PDFs locally. On Linux, `libpango`, `libcairo`,
  `libgdk-pixbuf` and `shared-mime-info`.
- **Production only:** PostgreSQL, and a TLS-terminating reverse proxy (nginx or Caddy).

---

## 4. Run it — development

### 4.1 Windows (PowerShell)

```powershell
git clone <your-repo-url> school-management-system
cd school-management-system

python -m venv .venv
.\.venv\Scripts\Activate.ps1            # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

pip install -r backend\requirements\dev.txt

Copy-Item .env.example .env             # then edit .env: set a real SECRET_KEY

cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000
```

### 4.2 macOS / Linux

```bash
git clone <your-repo-url> school-management-system
cd school-management-system

python3 -m venv .venv
source .venv/bin/activate

pip install -r backend/requirements/dev.txt

cp .env.example .env                    # then edit .env: set a real SECRET_KEY

cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000
```

Open **http://127.0.0.1:8000/**. Unauthenticated visitors get the school's public website;
`/login/` is the portal chooser.

Generate a key when you need one:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### 4.3 Background worker (only needed for bulk imports and fee reminders)

```bash
cd backend
python manage.py qcluster
```

Runs in a second terminal. If `qcluster` is not running, queued tasks (Excel validation,
credential and reminder emails) sit in the queue **silently** — nothing errors on the web side.

### 4.4 Configuration reference

`.env` is read from the project root by `django-environ`; production should inject real
environment variables instead. Defaults shown are the development defaults.

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | *required* | Never commit a real one. |
| `DEBUG` | `False` | `True` in dev only. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated; **required** in prod. |
| `DATABASE_URL` | *blank →* `backend/db.sqlite3` | `postgres://user:pass@host:5432/dbname` in prod (required there). |
| `CACHE_URL` | *blank →* local-memory | `redis://127.0.0.1:6379/1` for prod so workers share the cache. |
| `SCHOOL_NAME` / `SCHOOL_MOTTO` / `SCHOOL_PHONE` / `SCHOOL_EMAIL` | `My School` / `Excellence in Education` / empty | Fallbacks for the School Settings singleton. |
| `SCHOOL_CURRENCY` | `GHS` | ISO-4217 code used in money rendering. |
| `EMAIL_BACKEND` | `...console.EmailBackend` | Emails print to stdout in dev. |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USE_TLS` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `DEFAULT_FROM_EMAIL` | localhost / 25 / False / empty / `noreply@school.edu` | |
| `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `False` | `prod.py` forces these to `True` regardless. |
| `SENTRY_DSN` | empty | Sentry initialises only when set. |
| `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` | empty, `0.1` | |
| `CSRF_TRUSTED_ORIGINS` | `[]` | Set to `https://school.example.com` behind HTTPS. |
| `LOG_FORMAT` | `verbose` | `json` for ELK/Loki/CloudWatch ingestion. |
| `SLOW_REQUEST_THRESHOLD_MS` | `500` | `RequestTimingMiddleware` warns above this. |
| `DIRECTOR_KPI_CACHE_TTL` | `600` | Read at call time, so `.env` works even though it is not in the `env()` defaults. |

---

## 5. Seed demo data

The seeder is the fastest way to see every screen with realistic content. It writes **through the
real service layer**, so ledger entries, audit rows and business events are genuine.

```bash
cd backend
python manage.py seed_demo_data
# or from the repo root, from any directory:
python scripts/seed_demo_data.py
```

It creates one school (Greenfield Academy, `GHS`), 6 subjects, 4 classes (Basic 5, Basic 6, JHS 1,
JHS 2), 3 terms, 5 teachers, 10 pupils per class by default with guardians and parent accounts,
fee structures and posted invoices, a mix of paid/owing/settled accounts, result sheets in every
governance state, homework with submissions, announcements and message threads.

| Flag | Effect |
|---|---|
| `--students-per-class N` | 2–100 pupils per class (default 10). |
| `--dry-run` | Runs everything, then rolls every write back. |
| `--force` | Allow running under the production settings module (refused otherwise). |

It is **idempotent** (natural keys + a fixed RNG seed of `20260901`), so a second run adds nothing.
It seeds **no attendance**.

For statement-screening work there is a second seeder, `python manage.py seed_statement_scenarios`
(documented in [backend/README.md](backend/README.md)) — nine pupils in a dedicated class
covering every per-term statement state: settled, part-paid with carry-over, untouched, paid ahead, late,
awarded, reversed, draft, holding a credit.

Demo logins — these are the credentials `locustfile.py` expects, so change them before exposing the
app anywhere:

| Username | Password | Role | Login identifier |
|---|---|---|---|
| `admin` | `REMOVED-DEMO-PASSWORD` | Admin | email |
| `director` | `REMOVED-DEMO-PASSWORD` | Director | email |
| `finance` | `REMOVED-DEMO-PASSWORD` | Finance officer | email |
| `tch_001` … `tch_005` | `REMOVED-DEMO-PASSWORD` | Teacher | email |
| `stu_001` … | `REMOVED-DEMO-PASSWORD` | Student | **admission number** |
| `par_001` … | `REMOVED-DEMO-PASSWORD` | Parent | username |

**Start over** by deleting `backend/db.sqlite3`, re-running `migrate`, then seeding again. There is
no wipe flag because the ledger is append-only.

---

## 6. Testing and quality gates

All Python tooling runs **from `backend/`** — `pytest.ini` and `DJANGO_SETTINGS_MODULE` live there.

```bash
cd backend
python -m pytest -q                            # ~1,130 tests across 72 test files
python -m pytest --cov=apps --cov-report=term-missing
python -m ruff check apps config tests
python manage.py check
python manage.py makemigrations --check --dry-run   # must stay clean before commit
python -m pip-audit -r requirements/base.txt        # dependency vulnerabilities
```

Tests use `config.settings.test`, which builds a throwaway in-memory SQLite database and never
touches `backend/db.sqlite3`. Axes is off, MD5 hashers, plain static storage, and a 60-second KPI
cache TTL.

Notable suites that encode project rules rather than just behaviour:

- `backend/apps/core/tests/test_security_matrix.py` — the authoritative `URL_ROLE_MATRIX` of who may
  reach which URL; `backend/tests/e2e/test_role_matrix.py` expands it across the whole URLconf.
- `backend/tests/test_static_assets.py` — the offline/vendored-asset rules above.
- `backend/apps/dashboards/tests/test_director_finance_contract.py` — an AST guard that fails if the
  dashboard layer imports `apps.finance.models`/`services`/`selectors` or `openpyxl`/`weasyprint`, or
  does money arithmetic. **Only Finance calculates money**, and every other module reads figures through
  the `apps/finance/interface/` façade, which holds no arithmetic of its own.
- `backend/apps/finance/tests/test_accounts_seed.py` — pins the declared chart of accounts to the
  data migration that seeds it.
- `test_director_query_count.py` — pins query budgets.
- `backend/apps/core/tests/test_navigation_integrity.py` — every sidebar link resolves.

**Load test** (see [docs/performance.md](docs/performance.md)):

```bash
locust -f locustfile.py --host http://localhost:8000          # web UI on :8089
locust -f locustfile.py --host http://localhost:8000 --headless --users 50 --spawn-rate 5 --run-time 60s
```

Five weighted user classes model a real school day: students 40, parents 25, admin 10, director 5,
finance 5.

> There is **no CI pipeline** in this repository and no pre-commit config — the commands above are
> run by hand. `black`, `pre-commit`, `factory-boy`, `django-debug-toolbar` and `nplusone` are
> declared as dev dependencies; the toolbar is the only one wired in, at `/__debug__/` when `DEBUG`.

---

## 7. Security

### Identity and access
- **One `User` table with a `role` field** (`admin`, `director`, `teacher`, `student`, `finance`,
  `parent`) and no default role. A single table makes a shared login and a shared audit trail
  simple, and role changes cheap; it is enforced at the access layer, not the storage layer.
- **Custom authentication backend** (`EmailOrAdmissionBackend`): staff log in with email, students
  with admission number, parents with username.
- **Portal chooser** at `/login/` plus per-role portals at `/login/<slug>/`, driven by one `PORTALS`
  registry — the single source of truth for portal copy, accents and allowed roles.
- **`RoleRequiredMixin`** compares `request.user.role` against `allowed_roles` and raises
  **403 with an explicit message**. A wrong role is never a silent redirect, because a redirect
  teaches an attacker that the URL exists. No `@permission_required`, no `user_passes_test`
  anywhere — one mechanism, one place to audit.
- Superusers bypass the role check **only while their account has no role**; a role-tagged superuser
  is held to its role (pinned by a test). Django admin remains available at `/django-admin/` — move
  it to an unguessable path in production.
- Finance splits `FinanceReadMixin` (finance + admin + director) from `FinanceWriteMixin` (finance
  only): **Director may read money but never write it.**

### Sessions, passwords, transport
- `django-axes`: **5 failures, 1-hour cooloff**, keyed on `["username", "ip_address"]` rather than IP
  alone — one person mistyping five times must not lock out a whole staff room behind one NAT
  address. Resets on successful login; lockout returns a custom JSON 403; a `lockout.html` page exists
  for the browser path.
- `must_change_password` is enforced by both the login view **and** `ForcePasswordChangeMiddleware`
  (with an exempt-prefix list), so an issued credential cannot be used to browse.
- **Staff roles cannot self-reset passwords.** `/accounts/password_reset/` is overridden by
  `StaffBlockedPasswordResetView`; admin-only reset lives at `/accounts/staff/<pk>/reset-password/`.
  Students and parents keep self-service reset.
- Django's four password validators, minimum length 8, similarity/common/numeric checks.
- **Logout is POST-only** — a prefetched `<img>` cannot sign anyone out.
- CSRF on every form; `X_FRAME_OPTIONS = DENY`; `SECURE_CONTENT_TYPE_NOSNIFF`;
  clickjacking middleware.
- `prod.py` turns on `SECURE_SSL_REDIRECT`, `SECURE_PROXY_SSL_HEADER`, HSTS for one year with
  include-subdomains and preload, `HttpOnly` + `SameSite=Lax` session and CSRF cookies, and requires
  `CSRF_TRUSTED_ORIGINS` for HTTPS domains.

### Data-level protection
- **Row-level ownership checks (IDOR defence)** inside views and services: a student's identity is
  always taken from the login, never from a querystring; parents are scoped to
  `parent_profile.children_links`; homework file streaming resolves the pupil from the session;
  report cards resolve through `_resolve_student_for_results` and only serve **published** sheets.
- **Attachments are never reachable via a media URL.** Homework files are stored under UUID names and
  served only by permission-checked streaming views.
- **Money integrity:** all amounts are `Decimal(14,2)` and rounded in exactly one place
  (`apps/core/money.to_money`), so no screen can drift by rounding convention.
- **The ledger is append-only, enforced three ways:** (1) `JournalEntry`/`JournalLine` raise on
  saving an existing row and on `delete()`; (2) `AppendOnlyQuerySet` refuses `.update()`,
  `.delete()` and `.bulk_update()`; (3) SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers installed by
  migration `0007_*`. The Python guards are what protect PostgreSQL. Corrections are reversing
  entries — never edits.
- **Segregation of duties:** `ApprovalRequest` is a second-signature queue where Finance creates and
  Admin decides, for reversals and expenses above configured thresholds and for every write-off.
  Approving executes through the ordinary posting service, so the queue cannot post what the posting
  path would refuse.
- **Immutability of documents:** `Payment.reference` and an optional `idempotency_key` are unique, so
  a double-clicked form returns the first payment instead of a second receipt. `(student, term,
  fee_structure)` is unique on `Invoice`, so re-billing cannot silently duplicate.
- **Period locking:** postings into closed `FinancialPeriod`s, or outside
  `FinanceSettings.backdating_days`, are refused. Reopening requires Admin and a reason, and emits an
  event.
- **Exports are hardened against CSV/formula injection** — `apps/core/exports.py` is the only
  sanctioned spreadsheet path and forces any cell beginning `= + - @ TAB CR` to a string.
- **Every sensitive write is audited**: `AuditLog` records actor, action verb, model label, object ID,
  a JSON `change_summary` diff, `request_id` and IP address. Rows are never updated or deleted.
- `ATOMIC_REQUESTS` wraps each request in a transaction, so a failure mid-write cannot leave a
  half-posted journal.

### Operational
- `/healthz/` (liveness, always 200) and `/readyz/` (readiness, checks DB + cache, 503 on failure) are
  unauthenticated and excluded from Axes.
- Sentry is configured with `send_default_pii=False`.
- `media/`, `db.sqlite3` and `staticfiles/` are gitignored; `.env` is gitignored while `.env.example`
  is tracked.
- Passwords for demo accounts are documented *because* they are demo accounts — change them, and
  delete or lock the demo users before any real deployment.

---

## 8. Scalability, performance and operations

### Architecture that keeps it cheap
- **Monolith with strict layering**: `template → view (permissions only) → selectors.py (reads, never
  writes) / services.py (writes, `@transaction.atomic`, audit, events)`. Views hold no business rules,
  so the same rules apply whether the request came from a browser, a management command or a task.
- **No Redis dependency for jobs** — `django-q2` uses the ORM broker, so one process and one database
  is a complete deployment. `Q_CLUSTER` runs 2 workers, 300s timeout, queue limit 50, `catch_up=False`.
- **SQLite is a supported development target, not a production claim.** Postgres is required in prod,
  and `prod.py` fails fast without a `DATABASE_URL`. The SQLite-only ledger triggers are documented,
  with the Python guards as the portable enforcement.
- **WhiteNoise** serves hashed, compressed static files from the app process — no S3, no nginx
  static location required.

### Measured budgets (pinned by tests)
| Path | Query budget |
|---|---|
| `school_kpis` | ≤ 12 queries |
| `class_fee_summary` | ≤ 5 queries |
| Director home | ≤ 20 cold, ≤ 12 warm |

Aggregation is deliberately done in Python on already-scoped querysets rather than multi-join
`.annotate()`, because aggregation across joins fans rows out and produces wrong totals at school size.

### Caching
One cache key carries the expensive analytics: `director_kpis_term_{term.pk}`, TTL
`DIRECTOR_KPI_CACHE_TTL` (600s default). It is invalidated by exactly three writers —
`record_payment`, `reverse_payment`, `publish_sheet` — **not** by invoice posting, adjustments,
expenses, fee-structure changes or attendance. Set `CACHE_URL` to Redis in production so all Gunicorn
workers share it; with locmem each worker keeps its own copy and the dashboard will disagree between
workers.

### Indexing
Composite indexes added for the Module 6 workloads: `accounts_user(role, is_active)`,
`finance_invoice(term, status)`, `finance_invoice(student, status)`,
`finance_payment(student, payment_date)`, `finance_journal_entry(source_type, source_id)`,
`students_student(current_class, status)`, `students_guardian(parent, is_primary)`.

### Observability (ADR-004)
Target: one Linux server, no Kubernetes, no paid log aggregation.
- `RequestIDMiddleware` mints a UUID per request, stores it in `threading.local()` (cleared after the
  response), echoes `X-Request-ID`, injects it into every log record and onto every `AuditLog` row.
- `RequestTimingMiddleware` warns above `SLOW_REQUEST_THRESHOLD_MS` (500) with path, method, elapsed
  ms, user and request ID.
- Three deliberately separate audit paths so each answers one question: `AuditLog` (*what changed on
  this object*), `SystemEvent` + structured log (*what happened at school today*, written in a
  savepoint so a logging failure can never break a transaction), and `apps.events` JSON lines for
  finance (`payment_posted`, `payment_reversed`, `invoices_posted`, `results_published`) — routable to
  `/var/log/school/events.jsonl` with a `TimedRotatingFileHandler`, midnight rotation, 90 backups,
  without touching code.
- `LOG_FORMAT=json` emits one JSON object per line for ELK / Loki / CloudWatch.

### Production deployment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements/prod.txt

export DJANGO_SETTINGS_MODULE=config.settings.prod
export SECRET_KEY=... DATABASE_URL=postgres://school:pass@localhost/school \
       ALLOWED_HOSTS=school.example.com CACHE_URL=redis://localhost:6379/1 \
       CSRF_TRUSTED_ORIGINS=https://school.example.com EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend

cd backend
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
gunicorn config.wsgi:application --workers 3 --bind 0.0.0.0:8000
python manage.py qcluster        # separately, ideally as its own service unit
```

Workers: roughly `(2 × CPU cores) + 1`, bound behind nginx with TLS and gzip. Add the readiness probe
(`/readyz/`) to your load balancer. Scaling is horizontal and stateless **except** for `MEDIA_ROOT` —
put it on shared storage, or serve it from one node, before adding a second server.

### Backup and restore

```bash
bash scripts/backup_db.sh          # pg_dump custom+compressed, or SQLite online .backup
bash scripts/restore_db.sh backups/school_db_<timestamp>.pg_dump   # destructive, prompts for 'yes'
```

Both are bash (Linux/macOS/Git Bash). `BACKUP_DIR` (default `<root>/backups`) and `KEEP_DAYS`
(default 30) are configurable; the backup script prunes old files and carries a cron example in its
header. Restore makes a safety copy of the current SQLite file before overwriting and prints a
post-restore checklist (`migrate --check`, `curl /readyz/`, admin login spot-check, restart gunicorn).

---

## 9. Known gaps and limitations

Stated plainly, because they will bite someone:

- **Attendance has no screens.** Models, services and selectors are complete; `/attendance/` renders a
  "coming soon" page from a stub URLconf.
- **No REST API.** DRF is configured but unused. Anything that needs JSON must add endpoints.
- **`apps/public_site/` is dead code** — present on disk, absent from `INSTALLED_APPS` and from the
  root URLconf, so its routes are unreachable. `apps/public` superseded it.
- **Screen coverage is partial** — no student edit / withdraw / restore / delete screen; classes,
  streams and subjects are create-only (years have detail / delete / set-current, terms have edit /
  set-current); class-subjects can be removed but not edited. Fixes go through `/django-admin/`, which
  registers only academics and accounts.
- **Finance edges:** no refunds, no manual-journal UI, no bank-paid expenses (expenses always credit
  the Cash account), and `Invoice.Status.CANCELLED` exists but no flow ever writes it. Invoice-less
  adjustments are invisible to fee totals. An invoice's deadline is only as good as the fee items it was
  billed from: `FeeItem.due_date` is optional, and where the items are blank the invoice and all its
  lines are `null`, so aging falls back to `issued_date` (0 of 120 invoices in the seeded demo DB carry a
  due date). Overpayment is deliberately left unallocated while the journal credits the full
  amount. **Re-billing after revising a fee structure doubles `billed`.**
- **Fee reminders broadcast to every student/parent** regardless of the selection —
  `apps/finance/tasks.py` ignores `student_ids`.
- **The `fees` feature switch is partial, by design.** `SchoolSettings.fees_enabled`, read through
  `apps/core/features.py::feature_enabled("fees")`, replaces figures with a sentence on the Admin,
  Finance and Director homes and hides any sidebar entry that declares `feature: "fees"`. It does **not**
  gate the `/finance/` screens or the ledger itself — an off switch there would strand the accounting.
- Late-fee switches in Finance Settings are flags only — nothing is posted automatically.
- Teacher and Finance home pages still carry placeholder "–" cards and leftover "Phase 5" text.
- `xhtml2pdf` is in `base.txt` but imported nowhere; WeasyPrint is the only engine.
- The static-asset suite depends on vendored files that must not be re-downloaded from a CDN (§2).

---

## 10. Repository layout

```
school-management-system/
├── backend/                  # Django project — see backend/README.md
│   ├── manage.py             # default DJANGO_SETTINGS_MODULE=config.settings.dev
│   ├── config/
│   │   ├── settings/         # base.py / dev.py / prod.py / test.py
│   │   ├── urls.py           # root URLconf
│   │   └── wsgi.py, asgi.py
│   ├── apps/                 # 12 installed apps (core, accounts, academics, students,
│   │   │                     #  teachers, results, finance, homework, attendance,
│   │   │                     #  communications, dashboards, public)
│   │   └── public_site/      # dormant, not installed, not routed
│   ├── tests/                # cross-app integration + architecture tests
│   ├── requirements/         # base.txt / dev.txt / prod.txt
│   ├── media/                # uploads (gitignored)
│   ├── staticfiles/          # collectstatic output (gitignored)
│   ├── db.sqlite3            # development database (gitignored)
│   └── pytest.ini
│
├── frontend/                 # Django templates + static assets — see frontend/README.md
│   ├── templates/            # 153 templates: base, partials, auth, registration,
│   │                         #   dashboards/<role>/, features/<module>/, public/
│   └── static/               # css/, js/, vendor/ (Bootstrap 5.3.3, Icons 1.11.3, Chart.js 4.4.4)
│
├── docs/
│   ├── SYSTEM_GUIDE.md       # 996-line architecture, security and operations handbook
│   ├── performance.md        # Locust baseline, budgets, tuning checklist
│   ├── decisions/observability.md   # ADR-004 (Accepted, 2026-10-16)
│   └── user_guides/          # empty
│
├── scripts/
│   ├── backup_db.sh, restore_db.sh          # bash; Linux/macOS/Git Bash
│   ├── seed_demo_data.py                    # wrapper around manage.py seed_demo_data
│   ├── make_student_import_xlsx.py          # builds bulk-import fixtures
│   ├── gen_dashboard_templates.py           # historical scaffolder (create-only)
│   ├── gen_stubs.py                         # historical scaffolder — would overwrite live code
│   └── write_finance_models.py              # legacy generator with a hard-coded machine path; do not run
│
├── samples/                  # pre-generated import fixtures (.xlsx)
├── school-website/           # static "Springfield Academy" design prototype; unreferenced by the app
├── nova-cash/                # unrelated Next.js instant-loan demo — see its own README
├── locustfile.py             # load test; credentials must match the seeder
├── ruff.toml                 # line-length 88, target py313
├── .env.example              # tracked; copy to .env
└── .gitignore
```

### Two directories that are not part of the school system
- **`nova-cash/`** is an independent Next.js 16.4.3 / React 19.3.0 demo of an instant-loan product
  (Tailwind v4, ESLint 9, Vitest with 66 tests). It shares nothing with this project — no API, no
  database (it keeps its own `nova-cash/data/novacash.db` and a JSON ledger), no code. Run it with
  `cd nova-cash && npm install && cp .env.example .env.local && npm run dev` → http://localhost:3000.
  Its demo mode works with zero credentials. Do not edit the Django app to "fix NovaCash".
- **`school-website/`** is a hand-written static mockup kept for design reference. Nothing in
  `backend/`, `frontend/` or `docs/` references it. The live public site is `apps/public` plus
  `frontend/templates/public/` and `static/css/public_new.css`.

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| PDF routes return a plain-text **503** | WeasyPrint cannot load Pango/GTK. Install the GTK runtime (Windows) or `libpango`/`libcairo`/`libgdk-pixbuf`/`shared-mime-info` (Linux). This is intentional — the app never serves HTML as a `.pdf`. |
| `ImproperlyConfigured: Set the SECRET_KEY environment variable` | No `.env` in the project root, or it wasn't copied. `django-environ` reads `<root>/.env` only. |
| Bulk import / reminders never run, no error | `python manage.py qcluster` is not running. Tasks queue silently in the DB. |
| `CommandError: refusing to seed ... production settings` | Pass `--force`, or point `DJANGO_SETTINGS_MODULE` at `config.settings.dev`. |
| Emails contain no link / print to console | Dev uses `console.EmailBackend`; set `EMAIL_*` variables for SMTP. |
| `MultiValueDict`/duplicate receipt on double submit | Working as designed — `Payment.reference` and `idempotency_key` are unique; the second submit returns the first payment. |
| Login locked for an hour | Five failures in one hour for that credential + IP pair. Wait out the cooloff, or clear it: `python manage.py axes_reset`. |
| `MissingFileError` during `collectstatic` | A vendor file was re-downloaded and regained its `sourceMappingURL` comment, or a `{% static %}` path is wrong. See the offline-first rule in §2. |
| Director KPIs differ between workers | `CACHE_URL` is unset, so each worker has its own locmem cache. Point it at Redis in prod. |
| Old money figures after a fee-structure change | Re-billing after a revision doubles `billed` (§9). Adjust or reverse rather than re-billing. |
| Tests pass but a template is blank | Run `python manage.py check` and `makemigrations --check`; tests use a separate in-memory DB, so unapplied migrations only bite locally. |
| I edited a ledger row and the app refused | Intended. Corrections are reversing entries; `JournalEntry.save()` on an existing row raises. |

**Where the logs are:** there is no log file. In development, watch the `runserver` / `qcluster`
terminal — the verbose formatter prints `rid=` and `uid=` on every line. In production, stdout is
JSON. `AuditLog`, `SystemEvent` and Axes lockouts are rows in the database (timestamps are UTC,
while `TIME_ZONE = "Africa/Accra"` governs display).

---

## 12. Architecture decisions

`docs/decisions/` currently holds one ADR, `observability.md` (ADR-004). The four decisions the root
README historically promised as separate files — single-table User, double-entry ledger, results state
machine, django-q2 over Celery — are documented in **`docs/SYSTEM_GUIDE.md`** instead, and enforced by
tests. The reasons, in one line each:

- **One `User` table with a `role` field** — one login form, one audit actor column, cheap role
  changes; access control lives in the mixin layer.
- **Double-entry, append-only ledger** — school finance must survive audits and staff turnover; a
  reversing entry preserves both the mistake and the correction.
- **`ResultSheet` state machine** — marks are legally significant. Four explicit states with a
  `VALID_TRANSITIONS` map, locks, reasons for reopening, and one audit row per transition make the
  governance trail provable.
- **`django-q2` over Celery** — Excel validation and reminder emails are the only async work; an
  ORM broker avoids adding Redis and a message broker to a one-server deployment.

### Migration policy
**Add new migrations only. Never edit or delete an applied migration** — including the finance data
and trigger migrations (`0005_seed_chart_of_accounts`, `0007_chart_sync_legacy_settlement_and_more`).
Chart-of-accounts changes ship as a new migration plus a test that keeps `chart_of_accounts.py` in
sync with it.

### Git
The repository is git-tracked (520+ files). `db.sqlite3*`, `media/`, `staticfiles/`, `.env` and
`node_modules/` are ignored — so the working database you seed is **not** under version control and
tests are the only safety net for behaviour. Keep `git status` clean before a checkpoint and expect
`makemigrations --check` to be part of review.
