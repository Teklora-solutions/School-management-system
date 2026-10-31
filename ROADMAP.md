# Roadmap

Working plan for the School Management System, as of **2026-10-31**. Everything marked *verified* was
checked against the code on that date; anything else is labelled as recorded intent.

Companion documents: [README.md](README.md) (run it, what it does) ·
[CHANGELOG.md](CHANGELOG.md) (what has already happened) ·
[backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md) ·
[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) · [LICENCE](LICENCE)

---

## 1. How work moves through this repo

The owner and the agent work in **checkpoints**, and this roadmap is the queue those checkpoints are
drawn from.

1. **Inspection first.** Before touching a queued item, produce a report of what already exists — files,
   routes, tests, what is genuinely missing — and wait for the word **"approved"**. Several items below
   were found by inspection to be *already* partly built; that is exactly what this step is for.
2. **One checkpoint per turn.** A checkpoint is one reviewable slice: changed files, the tests that prove
   it, and what was verified how. Then stop for review.
3. **Tests are the proof, not the commit.** `cd backend && python -m pytest -q` before and after. A
   checkpoint is not done on red tests, and pre-existing failures are never marked as this checkpoint's
   work.
4. **Build on what is there.** No regenerating a module, no silently replacing an existing screen or
   service, no unrelated drive-by fixes. Migrations are **additive only** — never edit or delete an
   applied one.
5. **Deferred is deferred.** Items in §4 were found and consciously postponed by the owner. They do not
   get re-litigated or "fixed" as a side effect of something else.
6. Money behaviour is only ever changed inside `apps/finance/services.py`, and read by other modules
   only through `apps/finance/interface/` — the dashboards AST guard enforces it.

Version numbers: the repository does not tag releases yet (see Q-2 in §5). Until it does, CHANGELOG
entries are dated rather than semver'd.

---

## 2. Where the system stands today

| Area | Verified state |
|---|---|
| Public website | Live at `/` (home, about, programmes, gallery, contact) with admin editing at `/website/` |
| Login and portals | Portal chooser at `/login/`, per-role portals, forced first-login password change, staff self-reset blocked, Axes throttling keyed on credential+IP |
| Admin portal | Staff directory, School Settings, audit log, activity feed + daily report, six role dashboards |
| Academics | Years, terms, classes, streams, subjects, class-subjects — years/terms have lifecycle actions, classes/streams/subjects are create-only |
| Students | Roster, profile, admission PDFs, Excel bulk import with preview, promotion batches with idempotency |
| Teachers | Profiles, per-year assignments with one-teacher-per-subject enforced, matrix, distribution, workload, my-classes, marks entry |
| Results | Exam-only marks, `GradeScale` per year, `Draft → Submitted → Confirmed → Published` governance, report-card PDF with school identity + class position + CT remark + signature block, class broadsheet **PDF** with top-of-class callout |
| Finance | Double-entry append-only ledger, fee structures/elections/discounts/plans, invoicing, receipts, reversals, adjustments, expenses, budgets, vendors, periods, approval queue, gap-free numbering, reports + Excel, statements + receipt PDFs, fee reminders |
| Homework | Teacher authoring with publish/unpublish, 5-file caps, student/parent views, submissions with late flagging |
| Communications | Announcements with scheduling and read tracking, complaint/inquiry threads with status workflow and admin inbox |
| Attendance | **Models, services, selectors complete — no screens** (single `ComingSoonView` route) |
| Public REST API | **None.** DRF installed and configured; zero serializers, viewsets or API views |
| Test suite | **1,108 passed, 22 failed** on 2026-10-31 (~1,130 tests across 72 files). The 22 are pre-existing and are broken down in Q-1 |
| CI / CD | None. No `.github/`, no pipeline, no pre-commit config |
| Deployment | Documented for one Linux box (gunicorn + nginx + Postgres); no containers |

**Not part of this product:** `school-website/` is an unreferenced static mockup. `apps/public_site/` is a
dormant app — not in `INSTALLED_APPS`, not routed, superseded by `apps/public`. (`nova-cash/`, an
unrelated Next.js demo that briefly shared the repository, was removed on 2026-10-31.)

---

## 3. The queue

Sizing is deliberately rough: **S** ≈ under an hour of agent work, **M** ≈ one full checkpoint,
**L** ≈ several checkpoints.

### Now — finish what is already half-built

| ID | Item | Why it is first | Size |
|---|---|---|---|
| **A-0** | **Restore the public-website content contract** (the Module 5 · P5 redesign area) | The school's front door is the only area with a large red block: **17 failing tests** in `apps/public/tests/test_public_site.py` pin rules the current templates do not meet — a shared public shell (`app-wrapper--public`, no role sidebar leaking to a signed-in parent), "not written yet" placeholders instead of invented copy, counters that match the tables, gallery publish-order, footer portal links, and the logo appearing only once a file is attached. Whether the contract or the redesign is right is the first question to settle; then a one-line fix in the same pass: `fonts.gstatic.com` preconnect is not in the offline allow-list, so `tests/test_static_assets.py` fails too. Nothing else in the queue is red | M |
| **A-1** | **Attendance screens** (Module 3 · S0c) — register-per-class-per-day UI, teacher daily capture, class rate view, per-student history, and the rate card the student/parent/result screens already assume | The data layer is done and tested; only the templates/views/URLs are missing, so this is the cheapest large gap and it unblocks attendance on report cards (an option already offered and declined once, so confirm before adding it to the PDF) | L |
| **A-2** | **Student lifecycle screens** — edit, withdraw, restore, graduate/delete-with-audit for a `Student` | Today a typo in an admission number or a pupil leaving is a `/django-admin/` job or a re-import. Statuses exist in the model; the screens do not | M |
| **A-3** | **Academics edit screens** — class, stream and subject update views (and their delete guardrails) | Create-only today; corrections currently go through Django admin, which bypasses the service-layer audit trail | M |
| **A-4** | **Class-teacher remark entry UI** (RC4 part) | The `ClassTeacherRemark` model, migration and report-card *display* are live; there is still no way for a class teacher to type the remark. A displayed field with no input path is a half-delivered feature | S |
| **A-5** | **Teacher results analytics** (RC3 + RC4) — grade distribution per subject, per-student remark list, subject averages per class, previous-exam comparison incl. most-improved, class-teacher transcript | Approved rulings exist (comparison unit = previous exam of any name; published-only gate; all three meanings of "distribution remarks"). Nothing verified as built | L |

### Next — close the finance and access gaps that affect daily use

| ID | Item | Why | Size |
|---|---|---|---|
| **B-1** | **Fee reminders respect the selection** — `send_fee_reminder_task` accepts `student_ids`, logs them, then announces to every student and parent | A privacy bug in ordinary operation: owing families receive a broadcast meant for a subset. Small, self-contained fix | S |
| **B-2** | **Guard against double-billing on fee-structure revision** | Re-billing a class after editing its structure silently doubles `billed`. Needs either a version-aware skip or an explicit "amend invoice" path (a new `Invoice` revision line, never an edit of a posted one — the ledger is append-only) | M |
| **B-3** | **Invoice cancellation flow** | `Invoice.Status.CANCELLED` is declared but no code path ever writes it, so a wrongly-posted invoice can only be unwound by adjustments | M |
| **B-4** | **Refunds** | Money coming back out of the till has no representation; today an overpayment simply sits unallocated | M |
| **B-5** | **Late fees: finish the path from proposal to posting** — `selectors.late_fee_proposals` builds the rows; nothing displays or books them | `FinanceSettings` has `late_fees_enabled` / `late_fee_percent` / `late_fee_grace_days` and its own help text admits "nothing is posted automatically". The selector is reached only by tests and the `interface` re-export — no view, no template. Needs a review screen (Admin/Finance), an explicit approve-then-post service call that writes an `Adjustment`-style charge through the ledger, and a per-invoice cap. Bank-paid expenses, by contrast, are **already done**: `_post_expense_entry` credits `1010` Bank when `paid_via == BANK` (`apps/finance/services.py:2640`, covered by `test_postings.py:265-271`) | M |
| **B-6** | **Manual journal entries (Admin only)** with the same balancing, period-lock and approval rules | Needed for opening balances and audit corrections; today there is no way to record anything the screens do not already model | M |
| **B-7** | **`fees_enabled` should gate the Finance portal, not just dashboard figures** | The switch currently swaps dashboard numbers for a sentence while `/finance/` stays fully reachable, which is a confusing half-off state | S |
| **B-8** | **Detail-page access for Director and Finance** (deferred item D-1 — promote to roadmap work only with the owner's go-ahead) | `students:detail` and `teachers:detail` are still `AdminRequiredMixin`, so oversight roles browse lists but cannot open the pupil they are asking about | S |

### Later — capability, not repair

| ID | Item | Notes | Size |
|---|---|---|---|
| **C-1** | **Read-only REST API** | DRF is already installed and configured (session auth, pagination 25). A first slice worth doing: student results, own statement, announcements — each behind the same role mixins and the same published-only gate, with a serializer test per endpoint. Decide token strategy first; nothing exists today | L |
| **C-2** | **Email/SMS delivery for receipts, report cards and reminders** | Emails currently go to the console backend in dev; `django-q2` already provides the queue. Requires a provider decision and a template per document | L |
| **C-3** | **Parent portal deepening** — per-child homework and attendance rollups, fee payment receipts download | The "viewing as child" switch already exists; the screens behind it are thin | M |
| **C-4** | **Timetable / scheduling module** | Nothing exists. Subjects are assigned per class per year, but there is no period grid, and it is the most common follow-on request once marks work | L |
| **C-5** | **Discipline and remarks log** | The `ClassTeacherRemark` pattern (one free-text per student per term) generalises into a proper behaviour record with categories | M |
| **C-6** | **Multi-school support decision** | The system is deliberately *one school per deployment* (school identity is a pk=1 singleton, one database). If more than one school must be hosted, that is an architectural change — tenant scoping across every model — not a feature. Decide before building anything that assumes it | XL |
| **C-7** | **Report-card attendance line** | Explicitly offered and *not* chosen on 2026-10-30. Blocked behind A-1; re-confirm before implementing | S |

---

## 4. Explicitly deferred by the owner

Found during inspection, acknowledged, and postponed. They are recorded here so nobody mistakes them for
oversights — and so nobody "fixes" them silently inside an unrelated checkpoint.

- **D-1** Director/Finance can read list pages but `students:detail` / `teachers:detail` still 403.
  *Verified still open 2026-10-31* — both views keep `AdminRequiredMixin`
  ([`apps/students/views.py:57`](backend/apps/students/views.py#L57),
  [`apps/teachers/views.py:56`](backend/apps/teachers/views.py#L56)).
- **D-2** A **roleless** superuser (`createsuperuser`) passes every role gate — the bypass applies only
  when `role` is empty ([`apps/core/mixins.py:38`](backend/apps/core/mixins.py#L38)) — but has no landing
  page: every dashboard view requires a specific role and navigation is keyed by role, so such an account
  is stuck. Pre-existing; unchanged on purpose.
- **D-3** `AdjustmentForm.invoice` empty-dropdown defect: **already fixed** — the field's queryset is now
  repopulated per student
  ([`apps/finance/forms.py:344`](backend/apps/finance/forms.py#L344)). Listed here because the note that
  recorded it as open is stale.
- **D-4** Teacher and Finance home pages carry hard-coded "–" stat cards and leftover "Phase 5" copy.
  Known, cosmetic, deliberately not chased.
- **D-5** `xhtml2pdf` is declared in `backend/requirements/base.txt` and imported nowhere. Removal is a
  dependency change to be done deliberately, not as a by-product.
- **D-6** The `docs/user_guides/` directory is empty — per-role user documentation is a real gap,
  postponed rather than forgotten.

---

## 5. Quality and operations backlog

| ID | Item | Status detail |
|---|---|---|
| **Q-1** | **Clear the pre-existing red tests** | Ran the full suite on 2026-10-31: **1,108 passed, 22 failed in 3m49s** (Django 5.1.15, pytest 8.4.2) — byte-for-byte the same 22 recorded on 2026-10-29, so nothing has regressed since. Three distinct causes, all identified: **17** in `apps/public/tests/test_public_site.py` (which tests the **live** `apps/public` website, despite the filename — the current templates do not meet the content contract those tests pin, e.g. the `app-wrapper--public` shell class, blank-field placeholders, gallery counters and footer portal links); **4** in `apps/accounts` (`test_staff_management.py`: forced-password-change middleware ×2, staff-create password-required; `test_role_login.py`: pending-change landing); **1** in `tests/test_static_assets.py` — `public/base.html` preconnects to `https://fonts.gstatic.com`, which is not in the shrink-only allow-list (only `fonts.googleapis.com` is). See **A-0** in §3 |
| **Q-2** | **Tag releases and adopt semver** | History is four commits (`9ad7f9c` baseline 2026-10-29 → `85bdbf6`, `c7b8700` 2026-10-30 → `29d953b` 2026-10-31) with no tags. Until releases are tagged, the CHANGELOG is date-keyed |
| **Q-3** | **Delete `apps/public_site/`** | Not installed, not routed, no models, no migrations, no tests — referenced only by its own files. The similar-named `apps/public/tests/test_public_site.py` belongs to the **live** app and must not be touched by this cleanup |
| **Q-4** | **Delete or formally archive `school-website/`** | 8 static files of a "Springfield Academy" mockup referenced by nothing in `backend/`, `frontend/` or `docs/`. Decide: design reference (then say so in its own README) or delete |
| **Q-5** | **Bring `docs/SYSTEM_GUIDE.md` back in line with the tree** | Verified stale in several ways: it still says the finance UI is absent (there are 30 templates), that results entry has no teacher UI, that `fees_enabled` is inert, that there are no academics edit/delete screens, and 459 tests against an actual 1,130 collected. The three READMEs now hold the corrected version; the guide needs the same pass |
| **Q-6** | **Add CI** | No pipeline exists. A first workflow is mechanical: install `dev.txt`, `manage.py check`, `makemigrations --check --dry-run`, `ruff check`, `pytest`. Note WeasyPrint's native libraries must be installed in the runner or the PDF tests behave differently from locally |
| **Q-7** | **Wire up pre-commit** | `pre-commit` and `black` are declared dependencies with no config file and no `[tool.black]`; nothing runs automatically |
| **Q-8** | **Set up a real SMTP + backup schedule for deployment** | `EMAIL_BACKEND` defaults to console, so credential and reminder emails print to stdout unless configured; `scripts/backup_db.sh` carries a cron example but nothing is installed |
| **Q-9** | **Re-run the Locust baseline** | `docs/performance.md`'s before/after tables are explicitly labelled indicative, not measured, and the seed volumes it cites (200 students / 6 classes) do not match the seeder (4 classes × 10 by default). The CSV target `docs/perf_results/` does not exist |
| **Q-10** | **Resolve the `ruff.toml` / README Python mismatch** | `ruff.toml` targets `py313` and the repo venv is 3.13.7, while the documented floor is 3.12. Pick one and state it everywhere |
| **Q-11** | **Remove the seeded dev-database artifacts from disk hygiene** | `backend/` holds seven `db.sqlite3.bak-*` files and a `db.rc2-try.sqlite3` containing real password hashes and pupil data. They are gitignored, which is correct, but they should be pruned or moved out of the working tree |

---

## 6. Deliberately not planned

Recorded so the absence is understood as a decision rather than a gap:

- **Docker / Kubernetes.** Single-school, single-server by design. The README documents gunicorn + nginx
  + Postgres; nothing asks for containers.
- **A JavaScript front-end build.** Templates plus 84 lines of vanilla JS and vendored Bootstrap/Chart.js
  is the whole client. There is no SPA, and the offline-asset test suite enforces that it stays that way.
- **Continuous assessment / weighted marks.** Results are exam-only, one raw score out of 100 per
  subject. Introducing CA weighting changes grading, broadsheets, positions and report cards at once.
- **Shared multi-tenant hosting** — see C-6; it is an architecture decision, not a backlog item.
- **Self-service registration** for students or parents. Accounts are created by the school (staff
  directory, admission import); the password-reset path for staff is blocked on purpose.

---

*This file is a queue, not a contract. Items move up when inspection shows the groundwork is already
there — as it did for the report-card PDF, which turned out to need identity plumbing rather than a new
reporting engine — and items move down when the owner defers them.*
