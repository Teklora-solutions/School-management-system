# Changelog

## 2026-11-18 — Module 3, CP3: the public chat widget

**Added**

- **Widget markup** (`frontend/templates/features/assistant/widget.html`): one launcher, a dialog
  panel with the school name, the "AI assistant — not a person" notice, a language switch, the consent
  paragraph, the conversation log, suggestions, a callback form and a composer. All visitor- and
  provider-supplied words are written with `textContent`; the script has no way to inject markup.
- **Mount gate** (`templatetags/assistant_widget.py`, `models.WIDGET_GATE_CACHE_KEY`): the template is
  included once, from `public/base.html`, but renders nothing unless `chat.off_reason()` is clear. The
  answer is cached for 60 seconds and cleared by `AssistantSettings.save()`, so a switched-off school
  ships no markup, stylesheet or script and public pages pay at most one settings query per minute.
- **Styles** (`frontend/static/css/assistant_widget.css`): built on the public site's `--pub-*` tokens
  (light and dark), a phone-first full-screen panel and a desktop card, 44px touch targets, visible
  focus rings, `prefers-reduced-motion` and forced-colors handling. No external fonts, icons or CDNs;
  the launcher and buttons use inline SVG.
- **Behaviour** (`frontend/static/js/assistant_widget.js`): vanilla, no libraries. Fetches the widget
  state, solves the proof of work in small slices, streams the reply from `chat/send/` as SSE (JSON
  fallback), reuses the visitor's language from `localStorage`, offers the suggestions, attaches
  helpful / not helpful / report controls with an optional reason, opens the callback form, starts a
  new conversation, hides itself if the state reports the widget off mid-session, and announces each
  finished reply through the polite status region.
- **Tests**: 20 in `tests/test_widget_ui.py` — off by default; each gate alone keeps it off; mounted on
  all five public pages; never on staff dashboards; saving settings clears the cached decision; one
  settings query per minute (none on the next render); the base template includes it exactly once; no
  dashboard shell mounts it; no HTML-writing APIs; no external URLs; labelled dialog, log, status and
  launcher (including at phone widths, where its visible label is hidden); honeypots match the views;
  keyboard entry and Escape; no inline styles; the script speaks the server event names; both
  languages define the same words; suggestions drawn on first load; feedback chips named from the
  language pack.

**Fixed during the browser check** (all found on a throwaway database copy):

- the launcher had no accessible name at ≤640px because its label is hidden and the SVG is
  `aria-hidden`; it now carries `aria-label` / `data-i18n-label` so it stays named and translates;
- the suggested questions were hidden on first load because only a language switch redrew them;
- feedback chip labels were created from already-translated text and went blank after language
  tagging; they are now drawn from the language pack and update in place.

**Migration.** None. `makemigrations --check` reports no changes.

**Results (2026-11-18)**: `apps/assistant` **502 passed, 0 failed**. Full suite **3036 passed,
4 failed** — the two known `PromotionTests`, `test_public_layout_loads_the_local_icon_stylesheet`, and
`apps/core/tests/test_static_version.py::test_login_page_requests_a_fresh_copy_of_the_stylesheet`
(expects `css/base.css?v=`; the concurrently edited `frontend/templates/auth/login.html` loads
`css/public_new.css`). No assistant test fails.

**Browser check** (isolated copy, fake provider, desktop shell resized to 531px): launcher named and
translated, panel opened, consent and opening shown, suggestions rendered, a suggested question sent
and answered with the honest refusal and both languages, feedback sent with a reason, language
switched to Kiswahili (chrome, screen-reader labels and chips all changed), callback form completed
and stored with the server-written purpose, reload restored the conversation, "Start again" cleared
it and redrew the suggestions. **Not confirmed there**: the desktop-width panel (the browser window
was 531px wide) and a real-provider answer with citations (only the fake adapter was configured, so
every answer took the refusal path). The refusal line names the school from School Settings, which
read "My School" on the copy while the public pages showed demo content — a data difference in the
copy, not code.

**Not in this step**

- No handoff rules or routing (CP4), no human inbox, takeover or SLA (CP5).
- No deletion, export or retention command for chat data (CP6).
- Consent wording is still DRAFT; its approval name and date are placeholders until go-live.

## 2026-11-18 — Module 3, CP2: the public chat backend

**Added**

- **Public chat service** (`apps/assistant/chat.py`): one gate for the whole widget (`off_reason()`:
  kill switch → `enabled` → `public_enabled` → `widget_enabled` → approved consent wording →
  `public_visitor_daily_limit`), conversation and message serialisation, `begin()` (honeypot → proof of
  work → clean → length cap → per-visitor/IP/site limits → mask → store), `complete()` (the K3 answer
  pipeline with the Public audience and `Profile.PUBLIC`), `start_new_conversation()`,
  `record_feedback()` and `request_callback()`.
- **Widget endpoints** (`apps/assistant/chat_views.py`): `chat/state/`, `chat/send/`, `chat/new/`,
  `chat/feedback/` and `chat/callback/`. They accept JSON or form posts, are CSRF-protected, answer
  `Cache-Control: no-store`, and set the signed visitor cookie only when it is new.
- **Streaming**: `chat/send/` answers as server-sent events (`status`, `start`, `delta`, `done`) when
  asked. The reply is **verified before the first text is sent**; provider tokens are never relayed.
  JSON clients get the same verified text in one payload.
- **Visitor identity without tracking**: a signed `assistant_visitor` cookie (30 days, httponly,
  SameSite=Lax) holds a random key; only its SHA-256 hash is stored on the conversation. No IP address
  is stored on a conversation, message or callback row.
- **Cheap proof of work** (`apps/assistant/pow.py`): SHA-256 over a cached, single-use challenge bound
  to the visitor digest (`DIFFICULTY = 4` leading hex zeros, 10-minute life), used only when
  `widget_pow_enabled` is on.
- **Safeguarding stays mandatory for visitors**: a real hit on a public message creates a
  `SafeguardingAlert` with the masked question, and only the safe reply is returned. Honeypot and PoW
  refusals never reach the model.
- **Limits**: one key per request (`limits.get_visitor_key`/`ensure_visitor`) so a first-time visitor's
  check and count use the same key; the site-wide ceiling remains the cost backstop.
- **Private data by default**: visitor messages are stored masked (`body = body_masked`); callback
  details are the one consented exception, with a server-written purpose sentence naming the retention
  period. Feedback reasons are capped and tied to the visitor's own assistant message.
- **Tests**: 52 new in `tests/test_public_chat.py` (default-off, gating by each switch and consent,
  honeypot, PoW success/failure/replay, length caps, visitor/IP/site limits, usage recording, verified
  and refused answers, safeguarding alert, Kiswahili detection, cookie properties and hashing, SSE
  framing, feedback ownership, callback limits and dedupe, CSRF).

**Migration.** None. CP2 adds no model fields; `0006` was applied earlier the same day after a
separate go.

**Results (2026-11-18)**: `apps/assistant` **482 passed, 0 failed**. Full suite **3016 passed,
4 failed** — the two known `PromotionTests`, `test_public_layout_loads_the_local_icon_stylesheet`, and
`apps/core/tests/test_static_version.py::test_login_page_requests_a_fresh_copy_of_the_stylesheet`,
the last of which expects `css/base.css?v=` and now receives `css/public_new.css` from the
concurrently edited `frontend/templates/auth/login.html` (written 19:04 today, outside this module).
No assistant test fails.

**Not in this step**

- No widget interface yet (CP3): nothing is rendered on the public site.
- No handoff rules, inbox, takeover or SLA (CP4–CP5).
- Consent wording is still DRAFT and its approval name and date are placeholders until go-live.

## 2026-11-18 — Module 2, K3: hybrid retrieval, answers, the answer desk and gaps

**Added**

- **Hybrid retrieval** (`knowledge/retrieval.py`): a vector ranking (cosine over in-process JSON
  embeddings) and a keyword ranking (weighted term frequency, English/Kiswahili tokenising, case and
  punctuation insensitive, digit tokens weighted three times) merged with reciprocal rank fusion
  (`RRF_K = 60`). Named constants in one place: `TOP_K = 5`, `MIN_SCORE = 0.30`, `MAX_QUESTION = 4000`.
  A comment records that scores are not comparable across embedding models and that K4 calibrates them
  with real embeddings. Scope is applied as a database filter **before** any ranking; only READY,
  non-stale passages embedded with the current model are scored. Weak results are refused **with no
  sources attached**, so a caller cannot use passages from a refused search.
- **Answer pipeline** (`knowledge/answers.py`): safeguarding screen → retrieval → refusal check →
  fenced sources and masked question → one chat call → citation check. Unknown citation markers are
  stripped. `figure_check()` requires every number, amount, date part and time part in the answer to
  appear in the text of the cited passages. A missing valid citation, an empty answer or any unmatched
  figure marks the answer **unverified**. `deliver()` is the only reader-facing entry point and returns
  the fixed refusal for anything unverified. `KnowledgeGap` rows are written for refusals and
  unverified answers. The fixed refusal wording is in English and Kiswahili, school contact first, and
  is marked **DRAFT**.
- **Answer cache**: hashed keys over the audience signature (role + sorted class ids), the visible
  collection ids, a hash of the masked question, the embedding-model signature and a knowledge version.
  Cached passage ids are re-hydrated through the live scope and effective dates at read time (the
  chosen rule over short TTLs), so an expired or unapproved document drops out even while cached.
  Values hold no question and nothing about the asker. A cached refusal carries no passages.
- **Answer desk** (`knowledge/views.py`, `knowledge/answer_desk.html`): ask as Visitor, Parent,
  Student, Teacher, Finance, Director or Admin, with optional class ids and provider. The preview builds
  the same `Audience` object a real reader gets and runs the same retrieval/answer code — no admin
  bypass. Shows the reader-facing text, status, retrieval scores, citations, raw unverified answer and
  figure problems, provider statistics, a safeguarding **preview** (rule, safe reply, who would be
  alerted — nothing sent) and a gap notice. Desk usage is `Profile.CONSOLE`; desk gaps are tests.
- **Per-document check**: **Test this document** on the document page scores that document's own
  passages with the same hybrid method. The question embedding is the only provider call; no answer is
  generated, and the page says so.
- **Gaps screen**: open list with filter by state and an **Include test runs** toggle; resolve with a
  note; delete any gap (with confirmation). Only the masked question, role, class signature, language
  hint, reason, top score and test flag are stored. Resolved-gap erasure is proposed at **90 days**
  (`GAP_RETENTION_DAYS = 90`, **DRAFT**; nothing is deleted automatically).
- **Console controls (D9)**: a new `kill_switch` stops everything, including the answer desk, the test
  console and ingestion. The desks are exempt from `enabled`, `public_enabled` and `signed_in_enabled`,
  but have their own `console_daily_limit` (default 50 requests/day) and
  `console_monthly_budget_ksh` (default KSh 500, separate from the site-wide budget). **This changes
  the provider console's behaviour**: it used to be fully exempt from budgets, and now counts against
  its own cap and ceiling (and still the kill switch). Both are admin-editable on the settings screen.
- **Personal data in documents** (`knowledge/personal_data.py`): every document is scanned for emails,
  Kenyan phones, ID/admission-like numbers and likely person names next to pupil/parent/guardian words
  (English and Kiswahili), before anything is embedded. Findings record kind, heading, passage number
  and a **masked** snippet (every finding in the window is masked, not only the reported one); the raw
  value is never stored. A document with findings cannot be approved or embedded until an admin records
  the required confirmation — who, when and the exact wording. The add/edit screens carry the warning
  and say exactly what is sent. This adds fields to `KnowledgeDocument`.
- **Tests**: 72 new — `tests/test_knowledge_answers.py` (39: tokenising, digit weighting, RRF lift, the
  exact "Grade 4 term fee" case, audience/class scoping, expired/archived rows, figure and citation
  checks, unverified handling, cache isolation and read-time expiry, Kiswahili, safeguarding,
  prompt injection and fence escaping) and `tests/test_knowledge_desk.py` (33: desk access and the
  three audience-preview cases, safeguarding preview with no alert/email, console caps and kill switch,
  document check, personal-data scan/confirm gate, gaps).

**Migration (read this).** `assistant/0005_assistantsettings_console_daily_limit_and_more` contains all
K3 schema changes in one additive migration: nine added fields (`kill_switch`, the two console limits,
and six document personal-data fields), a help-text change on `enabled`, and the new `KnowledgeGap`
table with two indexes. `showmigrations` was run first, `sqlmigrate` was shown, the database was
backed up, and `makemigrations --check` and `manage.py check` are clean. **However,
`showmigrations assistant` already reported `0005` as applied on this workstation before the separate
approval for applying it was given** — a concurrent tool runs migrations in this shared folder
(recorded for `0004` as well). The change is additive only; nothing was dropped or altered. If you
would rather revert it, `python manage.py migrate assistant 0004` is available and needs your explicit
approval, because it drops the K3 columns and the gap table.

**Results (2026-11-18)**: `apps/assistant` **381 passed, 0 failed** (re-run after the cache fix). Full
suite **2913 passed, 6 failed** — the two known `PromotionTests`, plus four failures caused by the
concurrent public-site template rewrite outside this module
(`test_public_layout_loads_the_local_icon_stylesheet` and three `apps/public/tests/test_public_site.py`
tests against `frontend/templates/public/base.html`). No assistant test fails. The full-suite failure
count moved from 3 to 6 during this module because that concurrent work continued in the same shared
folder; the assistant suite total was unchanged.

**Results (2026-11-18, later — Module 3 CP1)**: `apps/assistant` **430 passed, 0 failed** (+49 new
model and settings-form tests). Full suite **2965 passed, 3 failed** — the two known `PromotionTests`
plus `test_public_layout_loads_the_local_icon_stylesheet`. The three `apps/public/tests/test_public_site.py`
failures from earlier in the day are gone; the remaining styling failure is still in the concurrently
edited `frontend/templates/public/base.html`, outside this module. Migration `0006` was **not** applied
for this run; it was applied later the same day after a separate go (see Module 3 CP2 below).

**Not in this step**

- No public or signed-in chat screen; the answer desk is admin-only (Modules 3 and 4).
- No evaluation set, scoring or first real results (K4); no calibration of `MIN_SCORE` or the weights
  has been done, and with the fake provider the numbers would be meaningless.
- No real provider call was made in building or testing this, and no real key exists in the repository.
- No model-based second-layer safeguarding detection.
- No automatic gap erasure; the 90-day period is a DRAFT proposal.
- The consent wording remains DRAFT and is shown to nobody.

## 2026-11-18 — Module 2, K2: reading, chunking, embeddings and the ingestion worker

**Added**

- **Text extraction** (`knowledge/extraction.py`): PDF through **pypdf** (an encrypted or scan-only
  PDF is refused with plain wording), Word `.docx` through **python-docx** (headings keep their level,
  tables become rows joined with `|`), UTF-8 text/Markdown, pasted text, question-and-answer pairs, and
  web pages. Every library failure becomes one plain sentence; no file path or raw exception reaches a
  screen. Nothing executes macros or renders a file.
- **Web-page fetching**: `http`/`https` only; every redirect hop (at most 3) is re-checked; the host
  must resolve only to public addresses (private, loopback, link-local and reserved ranges are
  refused); 15-second timeout; 10 MB streamed cap; only HTML, XHTML or plain text is accepted.
  Scripts, styles, navigation, headers, footers, forms, SVG and iframes are dropped; headings become
  `#` lines. The fetched text is kept in `body_text` so an admin can see what was read.
- **Chunking** (`knowledge/chunking.py`): about 800 characters per passage with about 120 characters
  of overlap, cut at paragraph/line/sentence/word boundaries; markdown headings start a section and
  every passage keeps its heading path (`Fees › Payment plans`); fragments under 40 characters are
  dropped; more than 60 passages is refused with a message asking the admin to split the document
  rather than silently truncating it.
- **Embedding path** (`gateway.embed_chain()`): the same ordered fallback, retries and circuit breaker
  as chat, in batches of 32 passages; a provider without an embedding model is skipped before it is
  called; every attempt writes one `UsageEvent` with `source=ingestion`,
  `profile=ingestion` and the embedding cost from the provider's embedding price; the passage text is
  never stored on the row. Ingestion uses the monthly budget and optional per-provider cap but does not
  require the assistant's global switch, so a knowledge base can be prepared before go-live.
- **Ingestion worker** (`knowledge/ingest.py`, `tasks.py`): only approved documents on approved,
  effective shelves are read; the whole passage set is replaced after a successful embedding, so a
  failed re-index can never leave old wording beside new wording; withdrawing approval, drafting or
  archiving a shelf removes passages immediately. Transient failures (provider unavailable, timeout,
  rate limit) are retried up to 3 attempts; extraction, chunking and permanent provider failures are
  recorded once. Each attempt leaves a `KnowledgeIngestRun` row (status, attempt, passages, tokens,
  plain reason) and a document state of waiting, reading, ready, failed or skipped.
- **Lifecycle hooks** (`knowledge/services.py`): creating, editing or changing the status of a
  document and saving a shelf queue or purge the right work. Re-saving an already-approved document
  does not re-embed it. Changing or deleting a provider's embedding model flags affected documents
  **Needs re-index**; a stale document is ignored until re-read.
- **Admin re-index action**: a POST-only **Read it again** button on the document page, refused while
  the document is being read and audited when accepted; the page shows the state, passage count,
  embedding model, read time, last error and the five most recent runs.
- **Command** `reindex_knowledge`: no arguments re-indexes stale documents, positional ids choose
  documents, `--all` chooses every approved document, `--now` reads immediately instead of queueing.
  Drafts and archived rows are skipped with a reason.
- **New tables** (`models.py`, migration `assistant/0004_knowledge_ingestion`): `KnowledgeChunk`
  (ordinal, heading path, text, character count, SHA-256, JSON embedding, model, dimension,
  embedded-at) and `KnowledgeIngestRun` (status, attempt, times, passages, tokens, plain error), plus
  the `ingestion` choices on `UsageEvent.source`, `UsageEvent.profile` and `SafeguardingAlert.profile`,
  and the widened `KnowledgeDocument.body_text` help text.
- **Dependencies**: `pypdf`, `python-docx` and `lxml` added to `requirements/base.txt`. No new service
  and no database extension; vectors are JSON arrays, as decided in K1.
- **Tests**: 81 across `tests/test_knowledge_extraction.py` (40: files, HTML, URL safety, fetching,
  chunking) and `tests/test_knowledge_ingestion.py` (41: embedding gateway, ingest, lifecycle, worker
  retries, re-index screen, command). All provider calls go through the fake adapter or patched HTTP.

**Migration**: `assistant/0004_knowledge_ingestion` adds two tables and three choice lists plus one
help-text change; no existing data is altered, and on SQLite the choice/help-text operations are
no-ops. It was generated on 2026-11-18 and reviewed with `sqlmigrate`. A concurrent `migrate` run from
the other tool working in this folder applied it to the local database the same day (13:49, after
`core/0021` and alongside `public/0004`), so the checkpoint's own "apply after a fresh backup" gate was
overtaken; `assistant_knowledgechunk` and `assistant_knowledgeingestrun` were empty after the migration.
`makemigrations --check` and `manage.py check` are clean.

**Results (2026-11-18)**: `apps/assistant` **309 passed**; full suite **2845 passed, 2 failed**, both
the known `PromotionTests`. No new failures.

**Not in this step**

- No retrieval, ranking, citations, refusal, confidence or answer cache (K3).
- No question set or scores (K4).
- No screen still where a parent, pupil, teacher or visitor asks anything.
- Nothing reads passages outside the background worker; the request path is untouched.
- No pgvector, no local model inference, no OCR for scanned PDFs.

## 2026-11-18 — Module 2, K1: knowledge shelves, private files and admin screens

**Added**

- **Four tables** (`models.py`, migration `assistant/0003_knowledge_base`): `KnowledgeCollection`
  (shelves), `KnowledgeDocument` (files, URLs, pasted text, FAQs), `KnowledgeDocumentVersion`
  (history) and `KnowledgeQAPair` (question-and-answer rows), plus class link tables and indexes.
- **Private file storage** (`knowledge/storage.py`): `KnowledgeStorage` writes under
  `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`) with UUID filenames; `url()` raises so
  no template can print a direct link.
- **Upload checks** (`knowledge/validators.py`): PDF, DOCX, TXT and MD; 20 MB; extension must match
  the file's first bytes (PDF header, DOCX zip, UTF-8 text). Not an antivirus; files are never
  executed or rendered.
- **Audience model** (`knowledge/roles.py`, `knowledge/scope.py`): canonical pipe-wrapped role strings
  so the filter is plain SQL on SQLite and PostgreSQL; the audience ceiling (a document may be
  narrower than its shelf, never broader) checked in the model, the forms and the services;
  `visible_documents()` selects approved, effective documents **before** any ranking exists and is
  covered by a hand-edited-row leak test.
- **Admin screens** (`knowledge/views.py`, six templates): shelves list and form; documents list with
  title/shelf/status/type filters; four add modes (file, web page, paste, questions and answers);
  document detail with versions, saved text, QA pairs, duplicate and stale warnings, status change;
  and the only download route (admin-only, attachment, `nosniff`, audited).
- **Services** (`knowledge/services.py`): duplicate fingerprinting on file bytes or pasted content,
  version snapshots on title change, status changes and downloads audited.
- **Settings**: `KNOWLEDGE_MEDIA_ROOT`.
- **Tests**: 53 across `tests/test_knowledge.py` (32) and `tests/test_knowledge_views.py` (21),
  including the four creation modes, private storage, audience ceiling, all visibility combinations
  and the download rules.

**Migration**: `assistant/0003` was reviewed with `sqlmigrate` and applied to the local database on
2026-11-18 after a fresh backup. It only creates four tables, two link tables and indexes; no existing
table is altered. `makemigrations --check` and `showmigrations` are clean, and the four tables were
empty before and after.

**Results (2026-11-18)**: `apps/assistant` **228 passed**; full suite **2764 passed, 2 failed**, both
the known `PromotionTests`. No new failures.

**Not in this step**

- No extraction, chunking, embeddings or ingestion worker (K2).
- No retrieval, answers, citations, confidence, refusal or answer cache (K3).
- No question set or scores (K4).
- No conversations, handoff or inbox tables; those are Module 3.
- Nothing reads the documents, and nothing is shown to a parent, pupil, teacher or visitor.

## 2026-11-18 — Module 1, Step 2: the pipeline behind the screens

**Added**

- **Model gateway** (`gateway.py`): default provider first then `priority` order; two retries with
  1 s/3 s backoff for timeout, network, rate-limit and server errors (not auth or bad request); a
  per-provider circuit breaker (3 failures in 60 s opens it for 5 minutes); one `UsageEvent` per
  attempt; a fixed all-providers-down message.
- **Real adapters**: OpenAI (`/v1/chat/completions`, `/v1/embeddings`), Gemini
  (`v1beta` `:generateContent`, `:embedContent`, `x-goog-api-key`), DeepSeek (OpenAI-shaped, chat only,
  embeddings refused with a plain message), generic OpenAI-compatible (admin base URL), and the fake
  provider with deterministic error modes. All tested against patched HTTP; no real call is made.
- **Secrets**: error mapping passes provider messages through `redact()`; `assistant_reencrypt_keys`
  command for batch rotation (dry run by default); key-scan test.
- **Budgets and limits**: kill switch, monthly budget and optional per-provider cap checked before any
  call with a cautious reservation; signed-in per-user daily limit; public limits by signed cookie
  (10/day), IP backstop (30/day) and site ceiling (300/day), Nairobi day boundary.
- **Safeguarding floor**: mandatory rule pack in code (English/Kiswahili/Sheng across six categories,
  coercion requiring another hit), admin-added rules (add-only), masked alerts, in-app safety
  notifications, email with limited retries, escalation timer with dashboard middleware fallback.
- **Alert closure and DRAFT retention**: an admin closes an alert with a recorded outcome (who, when);
  acknowledgement alone keeps it open. The proposed retention (12 months after closure, admin-set,
  `0` = off) erases only the masked excerpt and keeps the minimal record. `assistant_purge_alert_excerpts`
  is dry-run by default and also handles a single deletion request with `--alert-id`; an open or
  acknowledged alert is refused.
- **Clear provider failure messages**: the OpenAI/DeepSeek/Gemini adapters map HTTP status and network
  failures to plain wording (key rejected, wrong model name, quota/rate limit, timeout, network,
  server). Users still see one fixed message when every provider is down; admins see the specific
  reason on the test screen and in the usage row.
- **Startup refusal**: `assistant.E001` makes `manage.py check` fail, and so blocks deploy, when
  `ASSISTANT_ENCRYPTION_KEY` is missing while a saved key or an enabled assistant needs it.
- **PII masking** before any outbound text: emails, Kenyan phones, labelled IDs,
  admission paths, names after titles / "my name is" / "call me" / "I am" and after
  "my son/daughter/child" or "mtoto wangu", with counts shown in the console. The name
  patterns ignore case only in the trigger phrase, so "call me on …" is not mangled,
  and an ID label's own words ("number") are never taken as the identifier.
- **Prompt fencing** for user text and future sources.
- **Consent gate**: draft wording in `consent.py`, visible only when an approval flag, name and date are
  all set.
- **Screens**: settings (four switches, money, four limits, safeguarding contact, escalation, working
  hours, consent), providers (five kinds, three models, prices, order, region, delete guards), mandatory
  and custom safeguarding rules, alerts (admin/director), test console with masked-request preview,
  failed jobs, and a persistent banner on every staff page.
- **Assistant-only job failure records** (`AssistantJobFailure`) with an admin resolve action.
- **Cheap banner and escalation**: the warning banner state (safeguarding contact, open-alert
  count) and the escalation check share one cached read, refreshed at most once a minute. An
  ordinary admin page adds two queries a minute, not two per page; a warm page adds none. The
  two existing page-budget tests were raised by those two queries with the reason recorded.
- **New tables/fields**: `CustomGuardrailRule`, `SafeguardingAlert`, `AssistantJobFailure`, plus the new
  settings and provider fields — migration `assistant/0002`.

**Tests**: 175 test methods in 12 files covering crypto, storage/audit, screens and access, forms,
gateway resilience, provider shapes and failure wording, guardrails and masking (including the
documented worked example), limits, alerts, escalation, closure and retention, commands, the key check
and the key scan.

**Migration**: `assistant/0002` was applied to the local database on 2026-11-18 after review of
`sqlmigrate`. It only adds fields and three tables. On SQLite Django rebuilt 26 tables to add NOT NULL
columns with defaults; PostgreSQL applies plain `ADD COLUMN`. Before applying, all three assistant
tables had zero rows. `makemigrations --check` and `manage.py check` are clean.

**Results (2026-11-18)**: `apps/assistant` **175 passed**; full suite **2711 passed, 2 failed**, both
the known `PromotionTests`. No new failures. The first full run exposed the banner's per-page query
cost; it now shares a 60-second cached state with the escalation check, and the two page-budget tests
were raised by exactly those two queries with the reason in their comments.

**Not in this step**

- No conversation screens, knowledge upload, retrieval, record tools, handoff records, streaming, or
  second-layer model screening. Consent remains a draft and is shown to nobody.

## 2026-11-18 — Module 1: foundations and admin controls

**Added**

- New `assistant` app with the repository's usual layers: models, selectors, services, views,
  templates, providers.
- Admin screens at `/assistant/`:
  - settings (switch, house rules, default provider, budget and daily limit, handoff email);
  - provider list, add and edit;
  - connection test (one canned prompt through the gateway).
- Read-only sidebar entry **AI Assistant** for the admin role.
- Fernet encryption for provider API keys, with the key held in `ASSISTANT_ENCRYPTION_KEY`
  (comma-separated for rotation). Only the last four characters are stored or shown.
- `assistant_make_key` management command to generate a key.
- Fake provider (deterministic, in-process) and a registry that refuses any other kind.
- Usage log written on every gateway call; audit entries on every save.
- Migration `assistant/0001_initial` adding three tables. No existing app changed except the sidebar
  entry, the root URL include, and the app list.
- 32 tests across crypto, services and screens.
- Documentation folder `docs/ai-assistant/` and an AI Assistant section in `docs/SYSTEM_GUIDE.md`.

**Not in this module**

- No conversation screen, no knowledge upload, no retrieval, no record tools, no safeguarding
  detection, no handoff sending, no budget enforcement, no consent text shown to anyone.

**Notes**

- The assistant is off by default.
- `cryptography` added to `requirements/base.txt` (Apache-2.0 OR BSD-3-Clause; already present in the
  environment as a transitive dependency).

## 2026-11-22 — Module 4 completion pass: tools, tutor, drafts, suggestions

**Added**

- **12 new read-only tools** in `tools/registry.py`: timetable (student + parent-child), next
  lesson, class materials (tutor grounding), fee next-due date, parent week summary,
  teacher unfinished items (stream-aware), director community queue and engagement trend,
  and tutor-usage summaries for parents and teachers (counts + topic buckets only).
- **Model-driven tool selection**: `_select_tools_model()` shows the model the role-filtered
  catalogue and validates every returned name/param server-side; `_select_tools_keywords()`
  is the fallback when no provider is reachable.
- **Tutor hardening**: `_tutor_guard()` refuses graded work (in-progress quiz attempt or
  assessed-work phrasing) before any provider call; `_tutor_answer()` grounds replies in the
  student's own published class materials; `is_tutor` recorded on every tutor message
  (migration `0009`).
- **Confirm-to-save drafts**: `POST /assistant/me/draft-announcement/` creates an unpublished
  announcement for admin/director only, audited as `ai_draft`.
- **Signed-in prompt context**: role, current term, Nairobi date, children/classes, memory
  preferences and `SIGNED_IN_RULES` (no other person's data; no medical/legal/financial
  advice; live records state fetch time and source).
- **Personalised suggestions**: at most one related-screen link per answer.
- **Language toggle** (EN/SW) on the chat page, saved to memory.
- **Wiring**: "Ask the Assistant" nav entry and floating button now cover admin, director
  and finance too; `assistant_signed_in.css` extracted from the template.
- **Tests**: `tests/test_module4_additions.py` — 25 tests covering tool scoping, tutor
  refusal, model tool selection, draft endpoint permissions, suggestions and safety rules.

**Migration.** `0009_module4_signed_in_v3` adds `SignedInMessage.is_tutor`.
