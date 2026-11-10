# Changelog

All notable changes to the School Management System. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/); entries are **date-keyed rather than semver-tagged**
because the repository has no release tags yet (see [ROADMAP.md](ROADMAP.md) · Q-2).

Dates before 2026-10-29 come from the module working notes, not from git — the project only became a
repository on that day. Anything labelled *verified* was checked against the code.

---

## [Unreleased] — November 2026 (LMS Checkpoints A–C + Step 1 fixes)

### Added — Virtual School LMS

#### Foundational setup
- **Timezone changed** from `Africa/Accra` (GMT+0) to `Africa/Nairobi` (EAT, UTC+3). All streak day-boundary calculations depend on this. No stored data altered (all timestamps are UTC in the DB; only display/`localdate()` changes).
- **`learning_enabled`** flag already existed in `SchoolSettings`; added **`engagement_enabled`** (`core.0009`) and **`quizzes_enabled`** (`core.0010`) Boolean fields (both default OFF). Added corresponding entries to the features registry and `school_settings_values`.

#### Checkpoint A — Teacher course builder (`learning.0001`–`0005`)
- New `learning` app with initial migrations for `CourseModule`, `Lesson`, `LessonProgress`.
- Added `is_published` to `CourseModule`, `notes_format` (plain/markdown) to `Lesson`, and new `LessonAttachment` model (UUID storage, 5 max, 20 MB max, PDF/Word/PPT/Excel/image/MP3 allowed).
- Full teacher course builder: create/edit/reorder/delete modules and lessons, up to 5 file attachments per lesson served through authenticated streaming views, publish/unpublish per lesson or whole module, preview-as-student, duplicate lesson or module, copy whole course to another term.
- Student course outline with per-module progress bars, completion ticks, Continue button pointing to the next uncompleted lesson.
- Module/lesson create forms include a year+term selector so the teacher can build for any term.
- Responsive builder UI with move-up/down buttons (no drag; works on any phone), draft badges, empty state guidance.

#### Checkpoint B — Student lesson experience (`learning.0006`)
- Added `StudentLessonNote` model (private per-student per-lesson, `learning.0006`).
- **Markdown rendering**: `learning/markdown_utils.py` using `markdown>=3.5` + `bleach>=6.1`. Allowed HTML tags: headings, bold, italic, lists, code blocks, tables, blockquotes, links, images. All other tags stripped. `<a>` links get `target="_blank" rel="noopener noreferrer"` automatically. Scripts, iframes, `on*` attributes and style attributes removed unconditionally.
- Added `markdown>=3.5,<4.0` and `bleach>=6.1,<7.0` to `requirements/base.txt`.
- **Video embeds**: `learning/video_utils.py` handles all YouTube URL formats (`watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`), Vimeo, Google Drive, and any other link (shown as plain external link). All YouTube embeds use `youtube-nocookie.com` (privacy-enhanced — no cookies until play). Thumbnail from `img.youtube.com` (static CDN, no cookies — the only external image request on the lesson page).
- **Click-to-play**: video loads only when the student taps the thumbnail, saving data on weak connections.
- **Next/previous lesson navigation** at the bottom of every lesson page — arrows cross module boundaries automatically.
- **Student private notes**: autosaved 1.5 s after last keystroke via XHR, falls back to normal POST. Character counter. "Saved ✓" confirmation. Parents see the section but cannot write notes.
- **Course search**: searches lesson titles and notes text within a subject/term, runs in Python over the already-fetched course data (no extra DB query).
- Markdown **formatting toolbar** in the lesson editor (Bold, Italic, H2, Sub-heading, Bullet list, Numbered list, Link, Code, Table) — vanilla JS ~80 lines, no CDN, no library. Visible only when Markdown format is selected. Keyboard shortcuts Ctrl+B, Ctrl+I. Collapsible "How to format" hint.

#### Engagement layer (`learning.0002`)
- `ClassEngagementSettings` (per-class on/off, `streak_grace_days` default 1, show/hide bars and streak).
- `LearningStreak` (per-student: `current_streak`, `longest_streak`, `last_active_date`, `streak_broken_at`).
- Fair streak algorithm (`learning/streak_utils.py`): school days only (Mon–Fri inside a term), weekends and term breaks are transparent, configurable grace-day allowance. Points never go down.
- Student progress page (overall bar, per-module bars, streak, next-lesson button, welcome-back banner on reset).
- Teacher class progress view (completion %, streak, who hasn't started, who is behind <25%).
- Director engagement overview (which classes are active, average completion per class).

#### Class community feed (`learning.0003`)
- `ClassPost` (announcement/question/tip/celebration, `is_pinned`, `is_removed` soft-delete, `removed_by`/`removed_at`).
- `ClassPostReaction` (5 child-safe emoji: 👍 ✅ ❓ ❤️ 🔥; unique per user per post).
- Teachers can post all types; students can only post questions; parents read and react only.
- Teacher moderation: pin/unpin, soft-remove (invisible to students, greyed-out to teachers), restore.
- Rules banner shown once per session (sessionStorage), dismissible.
- Reactions update live via XHR; fall back to full-page POST if JS is off.
- Class Feed sidebar link added for teacher, student and parent roles.

#### Points, levels and badges (`learning.0004`)
- `PointConfig` singleton (all point values + `daily_feed_cap`, `pts_quiz_pass`, `pts_quiz_attempt` — editable by admin without code changes).
- `PointLedger` (immutable: every award or reversal is a row; `idempotency_key` unique constraint prevents double-award; `reversal_of` self-FK links reversals to originals).
- `StudentLevel` cache (recalculated from ledger after every award).
- 7 levels: Seedling (0) → Sprout (50) → Explorer (150) → Achiever (350) → Scholar (700) → Champion (1,200) → Legend (2,000).
- Points awarded for: lesson completion (10, once per lesson), homework on-time (20), homework late (8), feed question (5, daily cap 15), streak milestone 5/10/20/30/50 days (15 each), teacher bonus (1–20 with required reason).
- Removing a feed post reverses the student's feed_post points.
- Level-up in-app notification fires once per level (guarded by `notified_level`).
- Teacher: view class points table, full audit ledger per student, give bonus, reverse award (creates negative row, never deletes).
- Student: My Badges page (level card, all-levels display, recent history). Parent: same read-only.
- Director: Points Overview (per-class avg, top earner, engagement on/off).

#### Quizzes (`learning.0007`–`0008`)
- `Quiz` (OneToOne on Lesson, `pass_mark`, `attempts_allowed` 1/2/3/unlimited, `time_limit_minutes`, shuffle flags, `show_correct_after`, `is_published`, `counts_for_gradebook` stored but not connected to report cards yet).
- `QuizQuestion` (MC or True/False, up to 20 per quiz, `body` Markdown, `explanation`, `position`).
- `QuizChoice` (`is_correct` **never sent to browser before submission**).
- `QuizAttempt` (started_at, submitted_at, score_pct, passed, `answers_auto_submitted`, `question_order` JSON shuffle-seed for resume consistency).
- `QuizAttemptAnswer` (selected_choice FK, `selected_choices` M2M empty — reserved for future "select all that apply", `answer_text` blank — reserved for future short answer, `is_correct` server-only).
- **Edit-lock**: questions and choices locked after first submitted attempt. Teacher sees gold warning banner + "Duplicate quiz" button. Duplicate creates a fresh editable copy on a new lesson.
- **Server-side marking**: `submit_attempt()` reads `is_correct` and sets `QuizAttemptAnswer.is_correct` entirely server-side. Correct answers never reach the browser in any pre-submission response.
- **Timer**: server-clock-based (`time_remaining_seconds` property on attempt). JS countdown auto-submits via form POST when time reaches zero. `auto_submit_timed_out()` handles server-side forced submission.
- **Resume**: AJAX `save_answer` endpoint saves each answer as the student clicks. A student who closes the browser resumes with all saved answers and the timer still running.
- **Points**: `quiz_attempt:<quiz_pk>:<student_pk>` (first completed attempt, once) and `quiz_pass:<quiz_pk>:<student_pk>` (first pass, once) — idempotency keys prevent any double-award.
- Teacher results screen: per-student scores (sorted by best), class average, pass rate, who hasn't attempted, question difficulty ranking (sorted by wrong_pct descending).
- Parent view: child's quiz landing (attempt history, best score) and results page — read-only, no Take Quiz button.

### Changed
- `SchoolSettings` form (`core/forms.py`) updated: `engagement_enabled` and `quizzes_enabled` added to `Meta.fields` and the `required=False` checkbox list. Settings template (`features/core/settings.html`) shows both new switches in the Modules tab.
- `TeacherClassroomDetailView` and `StudentClassroomDetailView` rebuilt to load and display the full course outline from `course_selectors`.
- `teacher_classroom_detail.html` and `classroom_detail.html` rebuilt with responsive course outlines replacing the "coming soon" placeholder.
- `classrooms.html` and `teacher_classrooms.html` updated to show Class Feed buttons on each classroom card.

### Fixed
- Two `test_school_settings.py` tests updated to include `theme`, `engagement_enabled`, and `quizzes_enabled` in their POST payloads (pre-existing fragility exposed by new fields — the tests now correctly represent a "no change" save).
- `quiz_selectors.quiz_questions_for_student`: iterated `.values()` dicts now accessed with `a["key"]` not `a.key`.

### Verified — test suite
- **1,566 passed, 22 failed** as of Checkpoint C. The 22 are the identical pre-existing failures from 2026-10-31 (17 public site, 4 accounts, 1 static assets). Zero new failures introduced across all LMS checkpoints (A, B, C, Step 1 fixes).
- New test files: `apps/learning/tests/test_engagement.py` (57 tests), `apps/learning/tests/test_feed.py` (64 tests), `apps/learning/tests/test_points.py` (57 tests), `apps/learning/tests/test_course_builder.py` (45 tests), `apps/learning/tests/test_checkpoint_b.py` (49 tests), `apps/learning/tests/test_quizzes.py` (38 tests). Total new: 310 learning tests.

---

## [Unreleased] — 2026-10-31

### Added
- **`LICENCE`** — proprietary, all rights reserved, owned by **Teklora Solutions** (https://teklora.co.ke/).
  No usage, modification or redistribution rights are granted; a written agreement with Teklora Solutions
  defines any permitted use. It covers the
  data-controller responsibility for pupil and financial records, an explicit requirement to change the
  seeded demo passwords before real use, and the third-party licence table for the components the product
  depends on (Django, WeasyPrint, Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Chart.js 4.4.4, the fonts).
- **`ROADMAP.md`** — the working queue: how checkpoints move, a verified status table, Now/Next/Later
  items (`A-0`…`C-7`), the defects the owner explicitly deferred, the quality backlog, and what is
  deliberately not planned.
- **`CHANGELOG.md`** — this file, reconstructed from git history and the module notes.

### Changed
- **`LICENCE` ownership** — the copyright holder is named as **Teklora Solutions**
  (https://teklora.co.ke/) throughout: header, "NO LICENSE IS GRANTED", SCHOOL USE, DATA AND PRIVACY,
  NO WARRANTY and the CONTACT AND LICENSING address block. The earlier draft named an individual and told
  readers to substitute the commissioning entity; that instruction is gone, since the owner is settled.
  Version stamp moved to 1.1.

### Fixed
- **"Bank-paid expenses" was documented as missing — it is not.** The claim inherited from
  `docs/SYSTEM_GUIDE.md` §8.6/§20 was repeated into `README.md`, `backend/README.md` and ROADMAP **B-5**
  in the 2026-10-31 documentation commit. It is false: `Expense.paid_via` is a real `cash`/`bank` select
  (`apps/finance/forms.py:409`), `_post_expense_entry` credits `1010` Bank for a bank claim and `1000`
  Cash otherwise (`apps/finance/services.py:2640`), and `test_postings.py:275` walks every
  `PaymentVia` value. Corrected in all three files.
- **ROADMAP B-5 replaced** with the gap that actually exists: **late fees stop at proposals**.
  `selectors.late_fee_proposals` (`apps/finance/selectors.py:1671`) computes them and
  `FinanceSettings.late_fees_enabled` help text admits "nothing is posted automatically", but the only
  callers anywhere are tests and the `interface` re-export — no view, no template, no posting service
  (verified by repo-wide grep of `late_fee`: 0 hits in `frontend/`). `backend/README.md` likewise no
  longer calls it a "late-fee preview".

---

## 2026-10-31 — `29d953b`

The commit bundles three unrelated things: a results feature, the documentation set, and the removal of
a sub-project that never belonged to this product.

### Added — results
- **Class broadsheet PDF** — `results:broadsheet_pdf` (`BroadsheetPdfView` in
  [`apps/results/views.py`](backend/apps/results/views.py),
  [`features/results/broadsheet_pdf.html`](frontend/templates/features/results/broadsheet_pdf.html),
  166 lines of tests in `apps/results/tests/test_broadsheet_pdf.py`). Landscape A4, school identity
  header from `SchoolSettings`, and a **top of class** callout — this was RC2 of the report-card plan.
- A download button on the HTML broadsheet screen
  ([`features/results/broadsheet.html`](frontend/templates/features/results/broadsheet.html)) so staff
  reach the PDF from where they already read the data.

### Added — documentation
- **`README.md`** rewritten (596 lines): module-by-module functionality, full tech stack, Windows and
  macOS/Linux quick start, `.env` reference, seeder and demo logins, testing and quality gates, the
  security model, scalability and operations, an honest "known gaps" list, repo layout and
  troubleshooting.
- **`backend/README.md`** (605 lines, new): settings split, the `view → selectors / services` layering
  rule, the full access-control mechanism, per-app reference, the finance engine (chart of accounts,
  billing stack, ledger, reversal rules, money vocabulary), URL map, background jobs, PDF/export safety,
  observability, test architecture, and gotchas.
- **`frontend/README.md`** (295 lines, new): that this is a server-rendered template tree with **no build
  step**; the app shell and per-role accent theming; template naming conventions; the design-token
  stylesheet; the 84 lines of vanilla JS; the vendored offline-first asset rule and the test that enforces
  it; and how to add a screen end to end.

### Removed
- **`nova-cash/`** — a 37-file Next.js 16 / React 19 instant-loan demo that had been committed into the
  same repository by accident. It shared no code, no database and no API with the school system.
  *(The three READMEs written the same day were then corrected for this removal — an earlier draft
  described the folder as a sibling project.)*

### Fixed — documentation accuracy
Claims inherited from `docs/SYSTEM_GUIDE.md` were checked against the code and corrected in the new
READMEs. The guide itself is still stale in these places and is queued as ROADMAP item **Q-5**:

| Stale claim in the guide | Verified reality |
|---|---|
| "finance UI absent, all 26 templates missing, every finance page raises `TemplateDoesNotExist`" | 30 templates in `features/finance/`; `KNOWN_BROKEN` in `test_navigation_integrity.py` is `{}` |
| "results entry has no UI; teachers cannot reach any results screen" | `features/teachers/marks_entry.html` + `marks_list.html` exist and are routed |
| `SchoolSettings.fees_enabled` "is inert" | consumed via `apps/core/features.py::feature_enabled("fees")` on the Admin/Finance/Director homes and feature-tagged sidebar entries |
| "no edit/delete screens for years/terms/classes/subjects" | year detail / delete / set-current and term edit / set-current exist; classes, streams and subjects are the create-only ones |
| "`Invoice.due_date` is never set" | it *is* written from `FeeItem.due_date` at billing, but both are optional — 0 of 120 seeded invoices carry a date, so aging falls back to `issued_date` |
| "459 tests" | 1,130 collected across 72 test files |
| "`AdjustmentForm.invoice` queryset is permanently empty" | fixed — the queryset is repopulated per student in `apps/finance/forms.py` |
| "Record expense → **CR 1000 Cash — always cash, never bank**" (§8.6) · "no bank-paid expense" (§20) | false — `paid_via` is a real `cash`/`bank` choice on the form and `_post_expense_entry` credits `1010` Bank for a bank claim (`apps/finance/services.py:2640`, asserted by `test_postings.py:265-271`). **This one was carried into the new READMEs and ROADMAP by me and corrected under [Unreleased]** |

### Verified
- **`python -m pytest -q` → 1,108 passed, 22 failed in 3m49s** (Django 5.1.15, pytest 8.4.2,
  `config.settings.test`). This is the **same 22 pre-existing failures** recorded on 2026-10-29, i.e. no
  regression from the broadsheet work: 17 in `apps/public/tests/test_public_site.py` (the live website's
  content contract), 4 in `apps/accounts`, 1 in `tests/test_static_assets.py`. Causes are itemised in
  ROADMAP **A-0** / **Q-1**.
- HTTP smoke test against the running dev server: `/` 200, `/login/` 200, `/healthz/` 200, `/readyz/` 200,
  `/attendance/` 302 → login, `/website/gallery/` 302 → admin login.
- **Not verified:** no browser-level UI checking was done; screen correctness is inferred from templates,
  routes and status codes. PDF rendering itself could not be exercised on this machine — WeasyPrint cannot
  load `gobject-2.0-0` on Windows, so PDF routes answer their documented plain-text 503 here.

---

## 2026-10-30 — `c7b8700` — Results: report card PDF (RC1)

### Added
- **`ClassTeacherRemark`** model + `results` migration `0007_classteacherremark.py` — one free-text remark
  per student per term, unique on `(student, term)`, blank until a class teacher enters one.
- **`selectors.result_identity(request)`** — school identity for documents in one place: the
  `SchoolSettings` row wins, blank fields fall back to the `SCHOOL_*` settings, and the logo is resolved to
  an absolute URL because WeasyPrint cannot fetch a relative one.
- **`selectors.student_term_position`** — class position, ties sharing, rendered as "X / Y".
- 15 tests in `apps/results/tests/test_report_card.py`, using a recording fake for `weasyprint` so PDF
  *content* can be asserted without native libraries. That fake is the pattern later PDF work reuses.

### Changed
- **Report card PDF redesigned** (`features/results/report_card_pdf.html`, +171 lines): navy identity
  header with crest placeholder (first letter of the school name when no logo is uploaded), summary band
  (average, overall grade, total, subjects, class position), class-teacher name, **signature block** for
  class teacher and principal, the class-teacher remark, and a position column that appears only when
  `show_positions` is on.
- `apps/results/views.py` and `selectors.py` now feed the card from the settings row instead of the
  `SCHOOL_NAME` / `SCHOOL_MOTTO` environment variables, and honour `show_positions` — the two gaps that
  made the old card wrong for any school using the Settings screen.

### Migration applied
- `results/0007` applied to the real development database on 2026-10-30 (table confirmed present, server
  healthy afterwards).

---

## 2026-10-30 — `85bdbf6` — Teacher assignments

### Added
- Smart assignment form (class → subject → teacher, narrowed to what each teacher may hold).
- **Subject distribution** and **workload** pages, and the assignment matrix view.
- `set_class_teacher` flow to appoint a class teacher from the same screen.

### Changed
- **One teacher per class-subject per year** is now enforced by a database constraint
  (`teachers` migration `0002_*`) rather than by application politeness, so a second assignment to the same
  slot is refused rather than silently creating two marks sheets.
- Demo seeder adjusted alongside.

---

## 2026-10-29 — `9ad7f9c` — Baseline import (start of version control)

**520 files, 92,400 insertions.** Everything the project was up to this morning arrived in one commit,
because the directory was not a git repository before then. `.gitignore` was extended the same day to
exclude `db.sqlite3.bak-*` and `db.*-try.sqlite3` — seven 2 MB dev-DB snapshots holding real password
hashes and pupil data were about to be staged.

The modules below were already present at the baseline. Their dates are reconstructed from working notes:

### 2026-10-16 — Observability foundation (ADR-004, Accepted)
- `RequestIDMiddleware` (UUID per request, `X-Request-ID`, injected into every log record and every
  `AuditLog` row), `RequestTimingMiddleware` (warns above 500 ms), JSON logging through
  `python-json-logger`, the separate `apps.events` business-event stream, and Sentry in prod only when
  `SENTRY_DSN` is set (`send_default_pii=False`). Rejected: django-silk, Datadog/New Relic, an HTTP-log
  audit table, ELK.

### Through 2026-10-20 — Admin Portal checkpoints 1–5
1. Auth hardening — the superuser bypass narrowed to roleless accounts only.
2. Nav integrity — dead links pruned, finance Reports submenu, `SchoolReadMixin` for read-only oversight
   access, a real teacher-scoped "My Classes".
3. Admin-home fee KPIs actually rendered.
4. **Real School Settings page** (`core` migration `0003_schoolsettings`) — name, motto, phone, email,
   currency, show-positions, editable at `/school/`.
5. **`manage.py seed_demo_data`** plus the `scripts/` wrapper — idempotent, `--dry-run`, writes through the
   service layer, logins matching `locustfile.py`.

### Through 2026-10-20 — Student Portal S0a, S0b
- Events layer (`SystemEvent`, `core` migration `0004`), admin activity feed and daily report.
- **Bootstrap, Bootstrap Icons and Chart.js vendored locally** and 13 templates repointed, with the
  offline-asset contract test. Source-map comments stripped from the vendor files so `collectstatic` does
  not abort.

### 2026-10-21 — Academics uniformity A1
- Year picker extended, three terms per year, guarded year deletion (`academics` model/flow work; no
  migration). A2–A6 queued behind the Finance module.

### Through 2026-10-28 — Access and public site P1–P4
- Password policy, forced first-login change, staff self-reset blocked, portal chooser per role, and the
  school's public website (`apps/public`, `core` migrations `0005`, `0006`).
- Dashboard routing fixed on 2026-10-28 — `/dashboard/` stub, so clicking a login portal no longer bounces
  to the website. P5 (website redesign) was still in progress at the baseline; 17 tests in
  `apps/public/tests/test_public_site.py` currently fail against the redesigned pages — see ROADMAP
  **A-0** for the itemised causes.

### 2026-10-29 — Finance Portal (`apps/finance`, 10 migrations)
- Double-entry ledger with chart of accounts (`0005_seed_chart_of_accounts`), append-only enforcement plus
  SQLite triggers (`0007`), gap-free document numbering (`0008_numberseries_receipt_numbering_and_verification`),
  fee elections / discount rules / payment plans (`0009`), expenses / budgets / approval queue (`0010`).
- FS-1 fee elections, FS-2 fast Take Payment, FS-3 year/term reports and student statements delivered;
  FS-4's scenario seeder (`manage.py seed_statement_scenarios`) written the same day.
- Development database seeded on 2026-10-29 with four classes × 30 pupils (120 invoices) — the data still
  behind the local server.

### 2026-10-29 — Homework Portal H1–H4
- `Homework` with publish/unpublish and `published_at` (`homework` migrations `0002`, `0003`), up to five
  attachments per homework and per submission, UUID storage names with permission-checked streaming,
  student submissions flagged late at submit time and frozen after the due date, NEW badge scoped to the
  teacher's own assignments.

### Earlier baseline modules
- Academics (years, terms, classes, streams, subjects), Students (roster, guardians, bulk import,
  promotion with `idempotency_key`), Results (exam-only marks, `GradeScale` per year — migrations `0003`
  `0004` `0005` `0006`, the `Draft → Submitted → Confirmed → Published` state machine), Attendance
  (**model and services only — screens still missing**), Communications (announcements with scheduling and
  read tracking, complaint/inquiry threads), six role dashboards including the Director analytics suite,
  and the double-entry money vocabulary that later work pinned with tests.

---

## Migrations currently applied

| App | Migrations | Newest |
|---|---|---|
| `core` | 10 | `0010_schoolsettings_quizzes_enabled` |
| `accounts` | 3 | `0003_remove_user_accounts_user_role_active_idx_and_more` |
| `academics` | 2 | `0002_schoolclass_grade_scale` |
| `students` | 3 | `0003_remove_student_students_student_class_status_idx_and_more` |
| `teachers` | 2 | `0002_remove_teacherassignment_uniq_teacher_class_subject_stream_year_and_more` |
| `results` | 7 | `0007_classteacherremark` |
| `finance` | 10 | `0010_expense_claims_budgets_and_approvals` |
| `homework` | 3 | `0003_homeworksubmission_homeworksubmissionattachment` |
| `learning` | 8 | `0008_quiz_models` |
| `public` | 2 | `0002_contactmessage_handled_contactmessage_handled_at` |
| `attendance`, `communications` | 1–2 each | initial |

Policy: **additive only** — an applied migration is never edited or deleted, including the finance chart
seed and the SQLite append-only triggers.

---

*Next entries go under `[Unreleased]` until the first tag exists.*
