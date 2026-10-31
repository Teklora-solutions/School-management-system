# Frontend — templates and static assets

The user-facing layer of the School Management System. It is **not a JavaScript SPA**: this folder is a
tree of Django templates and static files that the Django process renders and serves. There is no
`package.json`, no bundler, no Tailwind, no npm and **no build step** — edit a file, reload the page.

Django finds it through three settings in
[`../backend/config/settings/base.py`](../backend/config/settings/base.py):

```python
FRONTEND_DIR  = PROJECT_ROOT / "frontend"
TEMPLATES[0]["DIRS"]        = [FRONTEND_DIR / "templates"]   # + APP_DIRS
STATICFILES_DIRS            = [FRONTEND_DIR / "static"]
```

No Django app carries its own `templates/` directory — every template in the product lives here.
See the [root README](../README.md) for the running instructions and
[`../docs/SYSTEM_GUIDE.md`](../docs/SYSTEM_GUIDE.md) for the architecture.

---

## 1. Layout

```
frontend/
├── templates/                       153 .html files
│   ├── base.html                    the app shell (130 lines)
│   ├── partials/                    5 shared snippets
│   ├── auth/                        5 standalone screens (login, portal chooser, lockout, …)
│   ├── registration/                4 standalone password-reset flow screens
│   ├── dashboards/<role>/           6 roles × exactly 5 files = 30 (the role shells + homes)
│   ├── features/<module>/           102 screens, 11 modules
│   └── public/                      6 pages of the school's open website
└── static/
    ├── css/    base.css (654) · public_new.css (448) · public.css (621, orphaned)
    ├── js/     base.js (84 lines — the whole JavaScript bundle)
    └── vendor/ bootstrap 5.3.3 · bootstrap-icons 1.11.3 · chart.js 4.4.4
```

`features/` per module: finance 30 · academics 12 · teachers 10 · results 9 · students 8 ·
communications 6 · accounts 5 · homework 5 · core 4 · public 4 · director 9.

---

## 2. Stack

| Need | What is used | Where |
|---|---|---|
| CSS framework | **Bootstrap 5.3.3**, vendored (not CDN) | `static/vendor/bootstrap/5.3.3/` |
| Icons | **Bootstrap Icons 1.11.3** + woff/woff2 fonts | `static/vendor/bootstrap-icons/1.11.3/` |
| Charts | **Chart.js 4.4.4** UMD | `static/vendor/chart.js/4.4.4/chart.umd.min.js` |
| Design system | ~650 lines of custom CSS with tokens | `static/css/base.css` |
| Public website theme | ~450 dense lines, own `--pub-*` tokens, dark **and** light | `static/css/public_new.css` |
| Behaviour | 84 lines of dependency-free vanilla JS | `static/js/base.js` |
| Typography | Inter (body) + Plus Jakarta Sans (headings) via Google Fonts `@import` | `base.css:8`, `public_new.css:2` |
| Template tags | `{% load static %}` (30×), `{% load humanize %}` (40×), `{% load core_extras %}` (2×) | only custom module: `backend/apps/core/templatetags/core_extras.py` |

There is **no** jQuery, no Alpine, no HTMX, no framework, no transpilation. There are **zero**
`fetch()` / `XMLHttpRequest` / `axios` calls in the entire template tree — every interaction is a
full-page form GET/POST with a CSRF token. (`djangorestframework` is configured in the backend but no
endpoint uses it, so there is nothing for a JS client to call.)

### Offline-first, enforced by tests
The school network may have no internet, so every vendor file is committed.
[`../backend/tests/test_static_assets.py`](../backend/tests/test_static_assets.py) fails the suite if:
- a template references `<script src="http…">` or `<link href="http…">`;
- a `{% static %}` path does not resolve;
- a vendored CSS references a font file that was not shipped;
- a vendored file declares a `sourceMappingURL` whose `.map` is not present.

The allow-list is exactly one entry, `fonts.googleapis.com` — cosmetic, since text falls back to system
fonts offline — and it is **shrink-only** (a test asserts every listed entry is still actually used).

> The trailing `//# sourceMappingURL=` comments were deliberately stripped from `bootstrap.min.css`,
> `bootstrap.bundle.min.js` and `chart.umd.min.js` because the `.map` files (~1.5 MB) are intentionally
> not downloaded; leaving them in makes `collectstatic` abort with `MissingFileError`. **Re-downloading
> those vendor files from a CDN breaks the build.**

---

## 3. The app shell

`templates/base.html` lays out a CSS grid: a 260px sidebar × 64px navbar over the main column.

```
<body data-role="{{ request.user.role|default:'' }}">
  {% block body %}
    <div class="app-wrapper {% block wrapper_class %}{% endblock %}">
      {% block navbar %}   mobile toggle · brand · navbar_links · navbar-spacer
                           · navbar_actions · role badge · avatar dropdown (Profile + POST logout)
      {% block sidebar %}
      <main class="app-main">
        {% include "partials/_messages.html" %}
        {% block content %}
      {% block footer %}
    </div>
    {% block extra_css %} in <head> · {% block extra_js %} before </body>
```

Blocks a page may override: `title`, `meta_description`, `wrapper_class`, `navbar`, `sidebar`,
`content`, `footer`, `extra_css`, `extra_js`, `body`.

`wrapper_class` exists so a page with no sidebar can collapse the grid instead of leaving a 260px
gutter empty.

### Per-role accent colour
`body[data-role]` is the only theming hook, and it is set by the shell from the logged-in user's role —
no cookie, no JS, no flash of the wrong colour:

| Role | Accent | Hex |
|---|---|---|
| admin | Indigo | `#6366f1` |
| director | Sky blue | `#0ea5e9` |
| teacher | Emerald | `#10b981` |
| student | Amber | `#f59e0b` |
| finance | Violet | `#8b5cf6` |
| parent | Pink | `#ec4899` |

Matching `.badge-role-<role>` classes exist for the navbar badge. An anonymous visitor gets the default
indigo.

### Role shells — `templates/dashboards/<role>/`
Each of the six roles has exactly five files, generated by
`../scripts/gen_dashboard_templates.py`:

| File | Content |
|---|---|
| `base.html` | 14 lines — extends the root `base.html`, fills `{% block sidebar %}` and `{% block footer %}` |
| `_sidebar.html` | 5 lines — `<aside class="app-sidebar">` including `partials/_sidebar_nav.html` |
| `_navbar.html` | Intentionally empty: a comment stating that the navbar lives in `base.html` |
| `_footer.html` | 3 lines — one-line copyright |
| `home.html` | The role dashboard: admin 221, director 354, finance 174, parent 234, student 215, teacher 136 lines |

### Which shell a feature page gets
Most `features/` templates (69 files) use `{% extends base_template %}`, where `base_template` is chosen
per request by the `base_template` context processor
([`apps/core/context_processors.py:36`](../backend/apps/core/context_processors.py#L36)). Another 27
hard-code `{% extends "dashboards/admin/base.html" %}`; the rest are includes, partials or PDF-only
markup. A page rendered with the wrong role shell is almost always a missing context, not a template
bug.

---

## 4. Naming conventions

- **Feature folders mirror backend apps**: `features/finance/`, `features/results/`, …, plus
  `features/director/` for the Director analytics suite, `features/core/` for school settings/audit
  screens and `features/public/` for the website-editing screens.
- **`<entity>_<verb>.html`**: `_list`, `_form`, `_detail` for CRUD; `_pdf` for PDF renders; `_preview`
  for two-stage import/upload confirmations; `<role>_<entity>_…` for role-specific variants
  (`homework/teacher_form.html`, `teachers/marks_entry.html`).
- **Leading underscore = private partial**, included but never routed
  (`features/finance/_period_filter.html` is the year/term GET filter).
- **Six templates are PDF-only markup**, rendered by WeasyPrint and never shown in a browser:
  `results/report_card_pdf.html`, `results/broadsheet_pdf.html`, `finance/payment_receipt_pdf.html`,
  `finance/student_statement_pdf.html`, `students/admission_form_pdf.html`,
  `director/books_report_pdf.html`. PDF CSS must be inline-in-template or in a `<style>` block —
  WeasyPrint does not run the app shell's JS and a 503 is returned if its native libraries are missing.
- `public/` templates are standalone (`<!doctype html>` + `data-theme`), as are all of `auth/` and
  `registration/`: they re-declare `<head>`, link the vendored CSS directly and load
  `bootstrap.bundle.min.js` + `js/base.js` at the bottom. `public/base.html` (411 lines) is its own
  shell with `pub-nav`, footer, back-to-top and one large inline script; it shares
  `partials/_footer_base.html`, which renders the wide four-column footer only when a view supplies
  `portal_links` (only `apps/public` does).

---

## 5. Design system (`static/css/base.css`)

Dark by default; there is no light theme for the app.

```css
:root {
  --font-body: 'Inter', system-ui, sans-serif;
  --font-heading: 'Plus Jakarta Sans', 'Inter', sans-serif;

  --color-bg: #0f1117;  --color-surface: #1a1d27;  --color-surface-2: #21253a;
  --color-border: rgba(255,255,255,.08);
  --color-text: #e2e8f0; --color-muted: #8896b3;

  --accent: #6366f1; --accent-light: #818cf8; --accent-dark: #4f46e5; --accent-glow: rgba(99,102,241,.25);
  --color-success: #10b981; --color-warning: #f59e0b; --color-danger: #ef4444; --color-info: #3b82f6;

  --sidebar-w: 260px; --navbar-h: 64px; --radius: 12px; --radius-sm: 8px;  /* + shadows, transitions */
}
```

Sections, in order: tokens → role accent overrides → typography → app shell grid → navbar → sidebar
and submenus → cards and `.stat-card` → buttons → forms → Bootstrap `.table` overrides through
`--bs-table-*` → footer → `.messages-container` toasts → login page → `.coming-soon` → utilities.

Rules that keep the system coherent:
- **Never hard-code a colour in a template.** Use a token, or `--accent` if it should follow the role.
- Restyle Bootstrap through its own custom properties (`--bs-table-*`) rather than fighting specificity
  with `!important`.
- The single mobile breakpoint is `991.98px` (Bootstrap's `lg`). Below it the sidebar becomes a fixed
  off-canvas drawer with an overlay.

`static/css/public.css` (621 lines) is **orphaned** — nothing references it. `public_new.css` is the
live website stylesheet: its own `--pub-*` tokens, amber `#f59e0b` + teal accents, `color-mix()`,
`clamp()` fluid type, and a real light theme via `[data-theme="light"]` overrides toggled by the inline
script in `public/base.html`.

---

## 6. JavaScript (`static/js/base.js`, 84 lines)

One IIFE, `"use strict"`, zero dependencies, loaded on every page. It does exactly four things:

1. Mobile sidebar open/close, Escape-to-close, overlay click to dismiss.
2. Sidebar `.has-children` accordion, auto-opening the group containing the active link.
3. Toast auto-dismiss after 5000 ms, plus manual close.
4. A generic `data-confirm` attribute → native `confirm()` before submitting the closest form.

Everything else is **inline `<script>` in the template that needs it** (26 template files), because a
shared JS file would have to know which page it is on. The two recurring patterns:

- **Chart.js dashboards**, loaded per page in `{% block extra_js %}`: `dashboards/director/home.html`,
  `features/director/{books_overview, class_fee_overview, class_performance, class_performance_drilldown}.html`.
- **Data passed from Django to JS via `<script type="application/json">`**, parsed on the client, used by
  `academics/year_form.html` (term-schedule preview) and `finance/payment_record.html` (pupil
  picker/search). Prefer this over string-interpolating into JS, which invites injection.

Pure-DOM helpers: `auth/login.html` password visibility toggle ·
`communications/announcement_create.html` audience → role/class block show-hide ·
`finance/fee_item_students.html` roster filter + select-all ·
`finance/fee_structure_form.html` dynamic fee rows with `label_N`/`amount_N` renumbering ·
`homework/teacher_form.html` class → subject/stream dependent selects ·
`results/grade_scale_form.html` inline-formset band rows via the `__prefix__` template ·
`director/class_fee_debtors.html` client-side total from table cells · `teachers/assignment_form.html`.

No `fetch`, no SPA routing, no state library. If you need async behaviour, add it to the page that owns
it — and note that any JSON endpoint you call must be created in the backend first.

---

## 7. Server-side behaviour the front end depends on

| Template need | Provided by |
|---|---|
| `{{ SCHOOL_NAME }}`, motto, logo, currency | `core.context_processors.school_settings` (falls back to `SCHOOL_*` settings) |
| The sidebar tree and active state | `core.context_processors.navigation` |
| Navbar unread badge | `core.context_processors.unread_count` |
| Parent "viewing as child" switcher | `core.context_processors.parent_children` + `accounts:select_child` (session-scoped) |
| `base_template` (which role shell) | `core.context_processors.base_template` |
| Money formatting | the `{{ value\|money }}` filter (`core/templatetags/core_extras.py`) over `to_money()`/`format_money()` in `core/money.py`, with the code from `currency_code()` (SchoolSettings wins over `SCHOOL_CURRENCY`); never re-format amounts in JS |
| Class positions on report cards | `SchoolSettings.show_positions` |
| CSRF | `{% csrf_token %}` on every form; logout is a **POST form** in the navbar dropdown |

Screens are permission-checked in the view: a wrong role gets an explicit **403 page**, not a redirect.

---

## 8. How to add or change a screen

1. **Backend first.** Add the route in `apps/<app>/urls.py`, a view with the right role mixin, and a
   `selectors.py` function for the read (views do not query business data directly). If the screen
   writes money, the write belongs in `apps/finance/services.py`.
2. **Add the URL to the security matrix** in
   `backend/apps/core/tests/test_security_matrix.py`. The e2e role-matrix test walks the whole URLconf
   and fails on an unmapped route — deliberately.
3. **Template** at `frontend/templates/features/<module>/<entity>_<verb>.html`, extending either
   `{% extends base_template %}` (role-aware) or `"dashboards/admin/base.html"` (admin-only screen).
4. **Sidebar entry** in `apps/core/navigation.py` so `test_navigation_integrity.py` keeps the link live.
5. **Styles** in `base.css` using existing tokens; a per-page override goes in `{% block extra_css %}`,
   not inline, and not in a new stylesheet (a new file must be vendored and registered or the static
   contract fails).
6. **Scripts** in `{% block extra_js %}` after the vendored `chart.umd.min.js` if you need charts.
7. **Verify against the running server** (`python manage.py runserver 8000`, then actually open the page
   as the role that owns it — plus once as a role that must be refused), and run
   `python -m pytest tests/test_static_assets.py apps/core/tests -q` from `backend/`.

Development is a plain template reload: Django's `{% templatetag %}`-free templates are read from disk
per request, and `DEBUG` serves static files straight from `frontend/static/`, so **no collectstatic is
needed locally**. Run `python manage.py collectstatic` only for deployment — it writes hashed copies to
`backend/staticfiles/` (gitignored).

---

## 9. Known rough edges

- `public.css` is dead weight (621 lines, referenced nowhere).
- `teachers/assignment_matrix.html` carries a leftover inline `<script>` whose only content is a comment
  about a `dict_get` shim; the filter it refers to lives in the core templatetags.
- Teacher and Finance `home.html` still render hard-coded "–" stat cards and leftover "Phase 5" copy.
- `_navbar.html` in every role shell is a no-op file kept only so the generated five-file layout stays
  symmetrical.
- `auth/password_reset.html` and `registration/password_reset.html` are both 44 lines and **not
  identical**; only the `auth/` one is reached by the custom `StaffBlockedPasswordResetView`.
- `attendance` renders `partials/_coming_soon.html` — the models exist, the screens do not.
- `../school-website/` is an unreferenced static mockup of a different school ("Springfield Academy")
  kept for design reference. It is not served and nothing imports it; the live public site is
  `templates/public/` + `public_new.css`.
- `../nova-cash/` is an unrelated Next.js demo app with its own toolchain. Nothing in this folder talks
  to it.
