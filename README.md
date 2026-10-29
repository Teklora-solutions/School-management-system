# School Management System

A clean, single-school management system built with **Python 3.12+**, **Django 5.x**, and **Bootstrap 5**.
No Docker. No over-engineering. Production-ready architecture.

---

## Quick Start (Development)

### 1. Prerequisites

- Python 3.12+
- Git

### 2. Clone & Set Up Environment

```bash
# Navigate into the project folder
cd school-management-system

# Create virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install dev dependencies
pip install -r backend/requirements/dev.txt
```

### 3. Configure Environment

```bash
# Copy the example .env
cp .env.example .env

# Edit .env — at minimum, set a real SECRET_KEY
# All other defaults work for local development
```

### 4. Database & Superuser

```bash
cd backend
python manage.py migrate
python manage.py createsuperuser
```

### 5. Run the Dev Server

```bash
# From the backend/ directory
python manage.py runserver
```

Open **http://127.0.0.1:8000/**. You'll be redirected to the login page.

### 6. Seed Demo Data

```bash
# From the backend/ directory
python ../scripts/seed_demo_data.py
```

Fills the database with one realistic school — 40 students in 4 classes, 5
teachers, term invoices with a mix of paid and owing accounts, result sheets in
every governance state, homework, announcements and inbox threads. The seed is
idempotent, so re-running it adds nothing. Every screen now has data behind it.

```bash
python ../scripts/seed_demo_data.py --dry-run   # run it all, roll it all back
python ../scripts/seed_demo_data.py --force     # allow running under prod settings
```

Demo logins (they match `locustfile.py`; change them before exposing the app):

| Username | Password | Role |
|----------|----------|------|
| `admin` | `AdminPass123!` | School administrator |
| `director` | `DirPass123!` | Director |
| `finance` | `FinPass123!` | Finance officer |
| `tch_001` … `tch_005` | `TeacherPass1!` | Teachers |
| `stu_001` … `stu_040` | `StudentPass1!` | Students |
| `par_001` … `par_038` | `ParentPass1!` | Parents |

To start over, delete `backend/db.sqlite3`, re-run `python manage.py migrate`,
then seed again (the ledger is append-only, so there is no wipe flag).

---

## User Roles

| Role | Accent Color | Description |
|------|-------------|-------------|
| **Admin** | Indigo | Full school administration |
| **Director** | Sky Blue | Analytics & oversight |
| **Teacher** | Emerald | Results, homework, attendance |
| **Student** | Amber | View results, fees, homework |
| **Finance Officer** | Violet | Ledger, payments, reports |
| **Parent/Guardian** | Pink | Children's progress |

---

## Project Structure

```
school-management-system/
├── backend/             # Django project
│   ├── manage.py
│   ├── config/          # Settings (base / dev / prod), urls, wsgi, asgi
│   ├── apps/
│   │   ├── core/        # BaseModel, mixins, middleware, audit log
│   │   ├── accounts/    # Custom User + profiles + login
│   │   ├── academics/   # Academic years, terms, classes, subjects
│   │   ├── students/    # Student records, guardians, bulk import
│   │   ├── teachers/    # Teacher records, class assignment
│   │   ├── results/     # Grading, state machine, report cards
│   │   ├── finance/     # Double-entry ledger, payments, receipts
│   │   ├── homework/    # Homework upload & view
│   │   ├── attendance/  # Daily attendance
│   │   ├── communications/ # Announcements, complaints
│   │   └── dashboards/  # Role-based analytics home pages
│   └── tests/           # Cross-app integration tests
│
├── frontend/
│   ├── templates/       # Django templates
│   │   ├── base.html    # Root skeleton
│   │   ├── partials/    # Reusable snippets
│   │   ├── auth/        # Login, password reset
│   │   ├── dashboards/  # One folder per role shell (5 files each)
│   │   └── features/    # One folder per feature
│   └── static/
│       ├── css/base.css  # Full dark design system
│       └── js/base.js    # Sidebar, toasts, interactions
│
└── scripts/             # DB backup, restore, seed data
```

---

## Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | *(required)* | Django secret key |
| `DEBUG` | `True` | Debug mode |
| `DATABASE_URL` | SQLite | DB connection URL |
| `SCHOOL_NAME` | My School | Shown in UI and emails |
| `SCHOOL_MOTTO` | Excellence… | Shown on login page |
| `SENTRY_DSN` | *(empty)* | Optional Sentry integration |
| `CACHE_URL` | local-memory | Django cache backend |

---

## Running Tests

```bash
cd backend
pytest
```

---

## Build Phases

| Phase | Status | Contents |
|-------|--------|---------|
| 1 | ✅ Done | Skeleton, settings, User model, login, 6 dashboard shells |
| 2 | ⏳ | Academics, Students, Teachers (CRUD, bulk import) |
| 3 | ⏳ | Results (grading, state machine, report card PDF) |
| 4 | ⏳ | Finance (double-entry ledger, payments, receipts) |
| 5 | ⏳ | Homework, Attendance, Communications |
| 6 | ⏳ | Director/Admin analytics dashboard, class promotion |
| 7 | ⏳ | Tests, logging, health check, seed data, docs |

---

## Architecture Decisions

See `docs/decisions/` (created in Phase 7) for ADRs on:
- Why single-table User with role field
- Double-entry ledger approach
- Results state machine design
- django-q2 over Celery

---

## Production Deployment

```bash
# Install prod dependencies
pip install -r backend/requirements/prod.txt

# Set environment variables (DATABASE_URL must be postgres://)
export DJANGO_SETTINGS_MODULE=config.settings.prod

# Collect static files
python manage.py collectstatic --noinput

# Run migrations
python manage.py migrate

# Start with Gunicorn
gunicorn config.wsgi:application --workers 3 --bind 0.0.0.0:8000
```

---

## Health Check

```
GET /healthz/
→ 200 {"status": "ok"}         (DB reachable)
→ 503 {"status": "error", ...} (DB not reachable)
```
