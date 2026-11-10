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
| Admin portal | Staff directory, School Settings (now includes learning/engagement/quizzes switches), audit log, activity feed + daily report, six role dashboards |
| Academics | Years, terms, classes, streams, subjects, class-subjects — years/terms have lifecycle actions, classes/streams/subjects are create-only |
| Students | Roster, profile, admission PDFs, Excel bulk import with preview, promotion batches with idempotency |
| Teachers | Profiles, per-year assignments with one-teacher-per-subject enforced, matrix, distribution, workload, my-classes, marks entry |
| Results | Exam-only marks, `GradeScale` per year, `Draft → Submitted → Confirmed → Published` governance, report-card PDF with school identity + class position + CT remark + signature block, class broadsheet **PDF** with top-of-class callout |
| Finance | Double-entry append-only ledger, fee structures/elections/discounts/plans, invoicing, receipts, reversals, adjustments, expenses, budgets, vendors, periods, approval queue, gap-free numbering, reports + Excel, statements + receipt PDFs, fee reminders |
| Homework | Teacher authoring with publish/unpublish, 5-file caps, student/parent views, submissions with late flagging. Points awarded on first submission through the engagement ledger. |
| Communications | Announcements with scheduling and read tracking, complaint/inquiry threads with status workflow and admin inbox |
| Attendance | **Models, services, selectors complete — no screens** (single `ComingSoonView` route). Checkpoint E will add the UI. |
| **Virtual School — course builder** | Full teacher course builder: modules, lessons (Markdown notes sanitised via bleach, YouTube/Vimeo/Google Drive embeds, up to 5 attachments per lesson), publish/unpublish/reorder/duplicate, copy course to another term, preview-as-student, formatting toolbar. Student course outline with completion ticks, Continue button, progress bars, private per-lesson notes with autosave, course search. |
| **Virtual School — engagement** | Streaks (school-days only, weekends/holidays transparent, configurable grace allowance), per-class on/off switch, teacher class-progress view (completion %, streak, who needs encouragement), student progress page, parent view. |
| **Virtual School — class feed** | Teacher posts (announcement/tip/celebration/question), student questions, 5 safe emoji reactions (child-safe), teacher pin and soft-remove, no private 1:1 chat. Points awarded for student questions with daily cap and removal reversal. |
| **Virtual School — points and levels** | 7 levels (Seedling → Legend), immutable point ledger with idempotency keys (no double-award), teacher manual bonus with required reason, reversal rows (never deletes), daily feed cap anti-farming, level-up in-app notification. Points from lessons, homework (on-time and late), feed questions, streak milestones, teacher bonus. Never from marks. |
| **Virtual School — quizzes** | Teacher quiz builder (MC + True/False, up to 20 questions, explanations, shuffle, pass mark, attempt limits, optional time limit, show/hide correct answers, gradebook flag stored). Edit-lock after first submitted attempt + Duplicate quiz. Student take-quiz with AJAX answer autosave, resume after disconnect, server-side auto-submit on timeout. Results: score, pass/fail, question-by-question review (gated by teacher setting). Teacher results screen: per-student scores, class average, pass rate, difficulty ranking. Points: `pts_quiz_attempt` (first attempt, once) + `pts_quiz_pass` (first pass, once). Correct answers never sent to browser before submission. |
| Public REST API | **None.** DRF installed and configured; zero serializers, viewsets or API views |
| Test suite | **1,566 passed, 22 failed** as of Checkpoint C (November 2026). The 22 are the same pre-existing failures from 2026-10-31: 17 `apps/public/tests/test_public_site.py`, 4 `apps/accounts`, 1 `tests/test_static_assets.py`. Zero new failures introduced across all LMS checkpoints. |
| CI / CD | None. No `.github/`, no pipeline, no pre-commit config |
| Deployment | Documented for one Linux box (gunicorn + nginx + Postgres); no containers |

**Not part of this product:** `school-website/` is an unreferenced static mockup. `apps/public_site/` is a dormant app — not in `INSTALLED_APPS`, not routed, superseded by `apps/public`.

---

## 3. The queue

Sizing is deliberately rough: **S** ≈ under an hour of agent work, **M** ≈ one full checkpoint,
**L** ≈ several checkpoints.

### Now — finish what is already half-built

| ID | Item | Why it is first | Size |
|---|---|---|---|
| **A-0** | **Restore the public-website content contract** | 17 failing tests pin rules the current templates do not meet. Nothing else in the queue is red. | M |
| **A-1** | **Attendance screens** — register-per-class-per-day UI, teacher daily capture, class rate view, per-student history | Data layer is done and tested; only templates/views/URLs are missing. This is Checkpoint E on the LMS roadmap. | L |
| **A-2** | **Student lifecycle screens** — edit, withdraw, restore, graduate/delete-with-audit | Statuses exist; screens do not. Corrections go through `/django-admin/`. | M |
| **A-3** | **Academics edit screens** — class, stream and subject update views | Create-only today; corrections bypass the service-layer audit trail. | M |
| **A-4** | **Class-teacher remark entry UI** | Model, migration and report-card display are live; no input path. | S |
| **A-5** | **Teacher results analytics** | Grade distribution, subject averages, most-improved, class-teacher transcript. | L |

### Virtual School — LMS checkpoints (in progress)

| ID | Item | Status |
|---|---|---|
| **LMS-A** | Course builder | ✅ **Done** — modules, lessons, attachments, publish/reorder/duplicate/copy |
| **LMS-B** | Student lesson experience | ✅ **Done** — rendered Markdown, click-to-play video, next/prev nav, private notes, search |
| **LMS-C** | Quizzes | ✅ **Done** — MC/TF, server-side marking, attempt limits, timer, points integration |
| **LMS-D** | Live classes and shared calendar | 🔲 Next — teacher schedules a session, students see Join button, shared calendar per class |
| **LMS-E** | Attendance screens | 🔲 Next — replaces the ComingSoonView stub using existing models |
| **LMS-F** | Gradebook link | 🔲 Later — quiz scores/homework feed into gradebook; teacher controls what counts |
| **LMS-G** | Virtual labs | 🔲 Later — PhET and other free simulations embedded in lessons, worksheet + lab report |
| **LMS-H** | Leaderboard | 🔲 Later — participation-only, opt-in, class-scoped, never mark-based |
| **LMS-I** | EduShare Africa | 🔲 Later — owner to provide EduShare stack details |
| **LMS-J** | Quality pass | 🔲 Later — notifications, parent weekly summary, director view, teacher onboarding, PWA |

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
| **Q-1** | **Clear the pre-existing red tests** | The 22 pre-existing failures are unchanged as of Checkpoint C (November 2026): **17** in `apps/public/tests/test_public_site.py`, **4** in `apps/accounts`, **1** in `tests/test_static_assets.py`. Zero new failures were introduced across all LMS checkpoints (A through C). The suite now passes 1,566 tests. |
| **Q-2** | **Tag releases and adopt semver** | History is four initial commits with no tags. Until releases are tagged, the CHANGELOG is date-keyed. |
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
