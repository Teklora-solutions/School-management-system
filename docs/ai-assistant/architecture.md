# Architecture

> **Status: approved design, 2026-11-18.** Module 1 Step 2 (the foundation) is built: model gateway,
> provider adapters, budgets, limits, guardrails, alerts, admin screens and the test console. Module 2
> K1 (knowledge data model, private storage and admin screens), K2 (reading, chunking, embeddings
> and the ingestion worker) and K3 (hybrid retrieval, answers, citations, the answer desk and gaps)
> are also built. The chat screens and record tools are still
> planned. This document marks **built** and **planned** throughout; nothing planned is described as
> if it exists.

## 1. Two assistants, one engine

| | Public assistant | Signed-in assistant |
|---|---|---|
| Audience | Website visitor, no account | Parent, student, teacher, finance, director, admin |
| Identity | Signed visitor cookie + IP address + consent notice | Django session, `accounts.User.Role` |
| Knowledge | Approved public documents only | Public + role-scoped and class-scoped documents |
| Records | None | Own records only, through permission-checked tools |
| Model | Cheaper "fast" slot | Chat slot (may be the same model) |
| Limits | Strict daily per-visitor (signed cookie), per-IP backstop and site-wide cap | Per-person daily limit |
| Retention | Shortest (no account to manage) | Longer, per the approved retention table |

The engine — gateway, retrieval, guardrails, handoff, budgets — is the same code. A **policy profile**
(planned `apps/assistant/policies.py`) supplies the differences: allowed knowledge scopes, allowed
tools, model slot, rate limits, consent text and retention. Policies are code, not admin-editable, so
the public assistant can never be reconfigured into reading a student's records.

## 2. Layers

The app follows the repository rule: reads through selectors, writes through services, permissions in
the view **and** the service.

| Layer | Files | Job | Status |
|---|---|---|---|
| Models | `backend/apps/assistant/models.py` | configuration, usage, alerts, custom rules, job failures | built (6 tables) |
| Crypto | `backend/apps/assistant/crypto.py` | Fernet encryption of provider keys, rotation | built |
| Providers (gateway) | `backend/apps/assistant/providers/` | framework-free adapters for fake, OpenAI, DeepSeek, Gemini and generic OpenAI-compatible servers | built |
| Gateway | `backend/apps/assistant/gateway.py` | ordered fallback, retries, backoff, circuit breaker, usage per attempt | built |
| Guardrails | `backend/apps/assistant/guardrails/` | mandatory safeguarding pack, admin rules, PII masking, injection fencing, safe reply | built |
| Alerts | `backend/apps/assistant/alerts.py` + `tasks.py` + `middleware.py` | alert rows, acknowledgement, escalation, email with recorded failures | built |
| Limits | `backend/apps/assistant/limits.py` | budgets, signed-in daily limit, public visitor + IP + site caps | built |
| Retrieval | `backend/apps/assistant/knowledge/` | K1 built: shelves, documents, private files, audience/class scope filter, version history. K2 built: extraction, chunking, embeddings, ingestion worker, re-index. K3 built: hybrid ranking, citations, figure check, refusal, answer cache, gaps, answer desk |
| Tools | `backend/apps/assistant/tools/` | read-only record tools wrapping existing services | planned (Module 4) |
| Policies | `backend/apps/assistant/policies.py` | the two assistant profiles | planned (chat modules) |
| Selectors | `backend/apps/assistant/selectors.py` | settings, providers, usage, rules, alerts, failures | built |
| Services | `backend/apps/assistant/services.py` | configuration writes, connection test, test console | built |
| Views | `backend/apps/assistant/views.py` + `knowledge/views.py` + `urls.py` | admin screens, director alerts, test console, answer desk, gaps, document check | built |
| Tasks | `backend/apps/assistant/tasks.py` | safeguarding email, its limited retry, and knowledge ingestion | built |

## 3. Request flow

The pipeline below is the whole design. Steps 1, 3, 4, 5 and 6 exist in
`knowledge/answers.py::answer_question()` and are exercised today by the admin answer desk. Step 2 is
enforced inside the gateway (`limits.check_console_limits` for the desks, `limits.check_budget` for
live profiles). Step 7 writes usage and gaps; message rows wait for the conversation module.

```
browser
  │  question + policy profile (role from session, or "public")
  ▼
view ──► service: assistant.answer()
           │
           ├─ 1. guardrails.screen()          safeguarding rules — MANDATORY, cannot be disabled
           │        └─ hit ──► fixed safe reply + alert + handoff, never an ordinary answer
           │
           ├─ 2. limits.check()               kill switch, rate limit, budget headroom
           │        └─ stop ──► plain message, no provider call, UsageEvent(outcome=blocked)
           │
           ├─ 3. retrieval.search()           scope filter BEFORE ranking (built in K3)
           │        └─ top N approved chunks with source ids
           │
           ├─ 4. prompt assembly              system rules + house rules + fenced DATA block
           │        └─ PII minimisation applied to everything outbound
           │
           ├─ 5. gateway.chat()               ordered providers, retries, circuit breaker
           │        └─ all down ──► "I can't answer right now…" + school contact
           │
           ├─ 6. output checks                citations + figures; failure = unverified
           │
           └─ 7. records                      UsageEvent (tokens, cost, latency, outcome);
                                              KnowledgeGap when refused or unverified;
                                              Message rows only after retention is approved
```

Writes that must survive a failed request (usage, safeguarding alerts) are committed in their own
transaction. The chat views will be marked `non_atomic_requests`, because production wraps every
request in a transaction (`backend/config/settings/prod.py:24`) and a slow provider call must not hold
one open.

## 4. Model gateway

**Built.** `providers/base.py` defines the contract every adapter satisfies; `gateway.call_chain()`
is the only outbound path.

| Capability | Status |
|---|---|
| Chat | built (`chat(messages, model, timeout, max_tokens)` returns text + token usage) |
| Embeddings | built (`embed(texts, model)`; DeepSeek refuses because it publishes no embeddings endpoint) |
| Error classification | built (`auth`, `rate_limit`, `timeout`, `network`, `bad_request`, `server`, `content_blocked`, `malformed`, `config`, `unsupported`) |
| Token counting | provider-reported counts; the gateway does not estimate |
| Streaming | **planned**; see section 10 |

Adapters built: `fake`, `openai`, `deepseek`, `gemini`, `openai_compatible` (base URL entered by the
admin). Model names were verified against provider documentation on 2026-11-18; the admin types the
exact model name, so the system never guesses one.

How the providers differ:

| | OpenAI | DeepSeek | Gemini | Generic OpenAI-compatible |
|---|---|---|---|---|
| Auth | `Authorization: Bearer` | same as OpenAI | `x-goog-api-key` header, not Bearer | usually Bearer |
| Chat endpoint | `/v1/chat/completions` | OpenAI-shaped path under `https://api.deepseek.com` | `/models/{model}:generateContent` with a different body | `/v1/chat/completions` |
| Usage in reply | prompt/completion counts | present | `usageMetadata` | sometimes absent |
| Embeddings | `/v1/embeddings` | **not offered**; adapter raises `unsupported` | `/models/{model}:embedContent`, one text per call | usually supported; some servers do not |

The gateway normalises all of this so services never see a provider's raw shape. API keys are
decrypted in the registry, live only inside the adapter, and any secret echoed back in a provider
error message is replaced with `[key]` before it can be stored.

**Fallback and resilience (built).**
- An ordered fallback list: the default provider first, then everything active by `priority`, then name.
- Timeouts: per provider, default 30 s (`timeout_seconds`).
- Retries: two, with 1 s then 3 s backoff, only for timeout/network/rate-limit/server errors — never
  for auth, bad-request, malformed or content-blocked errors.
- Circuit breaker per provider: open after 3 failures in 60 s, held open for 5 minutes; state in cache
  (Redis in production, locmem locally). An open circuit is skipped without a call.
- Everything down: `I can't answer right now. Please contact the school office and we will help you.`
  No stack trace, no provider detail beyond the plain classification.
- Every attempt writes a `UsageEvent`, including failed attempts.

## 5. Knowledge and retrieval (Module 2; K1–K3 built)

### 5.1 Built in K1 — documents, storage and scope

| Piece | Where | What it does |
|---|---|---|
| Shelves and documents | `models.py` (`KnowledgeCollection`, `KnowledgeDocument`, `…Version`, `KnowledgeQAPair`) | Catalogue, audience, status, effective dates, owner, versions, FAQ pairs. |
| Private files | `knowledge/storage.py` | `KnowledgeStorage` writes under `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`), with UUID filenames. `url()` raises, so no template can print a direct link. |
| Upload checks | `knowledge/validators.py` | PDF (`%PDF-` header), DOCX (ZIP containing `word/document.xml`), UTF-8 text; extension must match bytes; 20 MB cap. Not an antivirus. |
| Audience rules | `knowledge/roles.py`, `knowledge/scope.py` | Canonical role strings, the audience ceiling, the class rules, and `visible_documents()` — the SQL filter that runs **before** any ranking. |
| Forms and services | `knowledge/forms.py`, `knowledge/services.py` | One form per source type; duplicate fingerprinting; version snapshots; audit entries; the ceiling re-checked at write time. |
| Screens | `knowledge/views.py`, `templates/…/knowledge/` | Shelves, documents, four "add a document" modes, detail with versions and QA, status change, and the only download route (attachment, `nosniff`, audited). |

Only **approved documents on approved, effective shelves** are selected. Drafts, archived rows, expired
windows, unapproved shelves and documents above their shelf's audience are excluded in SQL before any
similarity work exists.

### 5.2 Built in K2 — reading and indexing

| Piece | Where | What it does |
|---|---|---|
| Extraction | `knowledge/extraction.py` | PDF (pypdf), DOCX (python-docx, headings and tables kept), UTF-8 text/Markdown, pasted text, active question-and-answer pairs, and web pages. Every failure is one plain sentence; no path or raw library error escapes. |
| Web pages | `knowledge/extraction.py::fetch_url` | `http`/`https` only; at most 3 redirects, each hop re-checked; the host must resolve only to public addresses; 15 s timeout; 10 MB streamed cap; HTML, XHTML or plain text only. Scripts/styles/navigation are dropped and headings become `#` lines. The fetched text is kept in `body_text`. |
| Chunking | `knowledge/chunking.py` | ~800 characters, ~120 overlap, cut at paragraph/line/sentence/word boundaries; heading paths (`Fees › Payment plans`); fragments under 40 characters dropped; >60 passages refused with "split this document" wording instead of truncating. |
| Embedding | `gateway.embed_chain` | Same ordered fallback, retries and circuit breaker as chat; batches of 32; a provider without an embedding model is skipped before the call; one usage row per attempt with `source=ingestion`; never stores passage text on the row. |
| Worker | `knowledge/ingest.py`, `tasks.py` | Reads only approved documents on approved, effective shelves. The whole passage set is replaced after a successful embedding; withdrawing approval purges it at once. Transient failures retry up to 3 attempts; each attempt leaves a `KnowledgeIngestRun`. |
| Hooks and re-index | `knowledge/services.py`, `knowledge/views.py`, command `reindex_knowledge` | Saves, status changes and shelf changes queue or purge the right work; a POST-only **Read it again** action and a command re-read one document, the stale ones or all approved ones. |

**Request path.** None of this runs in a request. A save queues work with django-q2 (after commit) and
the admin sees *waiting* until the background worker finishes. The worker must be running
(`python manage.py qcluster`); with no worker a document stays "waiting" and nothing fails loudly.

**Cost and budget.** Embedding is priced per million tokens from the provider's embedding price and
the USD→KSh rate. The same monthly ceiling and optional per-provider cap apply, with a cautious
reservation before the batch. Ingestion deliberately does **not** require the assistant's global
switch: an admin can prepare the knowledge base, and prove the pipeline, while chat is off.

**Honest limits.** Scanned PDFs are refused (no OCR); a document over 60 passages must be split;
extraction quality depends on the file; and the embedding model must stay stable per document —
changing it flags every affected document **Needs re-index**.

### 5.3 Built in K3 — hybrid retrieval and answers

```
question
  │
  ├─ mask PII; safeguarding screen (a hit stops here)  ← none of the rest runs
  │
  ├─ scope: audience built from the same code a real reader uses
  │        (role + sorted class ids + visible shelves)  ← database filter FIRST
  ▼
eligible, READY, non-stale passages only
  │
  ├─ embed the masked question (one embedding call, cached 30 days by model+text)
  ├─ vector ranking: cosine(question, passage) in Python, per passage
  ├─ keyword ranking: weighted term frequency over passage text
  ├─ merge with reciprocal rank fusion (RRF)
  ▼
top 5 passages with label S1…S5, title, heading and both scores
  │
  ├─ weak result → refusal, NO chat call, gap recorded
  ▼
system prompt + fenced SOURCES (data, never instructions) + fenced masked question
  │
  ▼
one chat call → citation check (unknown markers stripped, valid ones kept)
  │
  ├─ no valid citation, or a figure not in the cited text → UNVERIFIED
  │        └─ never shown to a reader; fixed refusal used instead; gap recorded
  ▼
verified answer + citations (and the answer cached for 1 hour, re-validated on read)
```

**Named constants, one place.** `knowledge/retrieval.py` holds `TOP_K = 5`, `MIN_SCORE = 0.30`,
`MAX_QUESTION = 4000`, `RRF_K = 60` and `KEYWORD_DIGIT_WEIGHT = 3.0`. A comment there states plainly
that scores are **not comparable across embedding models**: `MIN_SCORE` only means something for the
model in use, it is a conservative floor rather than a tuned value, and K4 calibrates it and the
weights on the school's evaluation set with **real** embeddings. The fake provider's numbers are
meaningless for calibration.

**Method — hybrid search.** The two rankings are built from the same already-scoped passages:

- **Vector ranking.** Cosine similarity between the question embedding and each passage embedding.
  Only passages embedded with the current model and dimension are compared; stale documents are
  excluded before scoring.
- **Keyword ranking.** Simple term-frequency over the passage text. Text is tokenised on letters,
  digits and apostrophes, lower-cased and split identically for English and Kiswahili; a short
  stopword list in both languages is dropped. A token containing a digit (a fee amount, a date part,
  a class number) counts **three times**, because an exact figure matters more than a vague match.
- **Merge.** Reciprocal rank fusion: each passage scores `1/(60 + position)` in each list it appears
  in, and the two scores are summed. RRF uses only order, so a keyword hit lifts a passage the
  vectors missed, and a passage strong in both rises above one strong in only one.

This is what makes *"Grade 4 term fee"* retrieve the passage with that exact class and figure even
when a semantically similar passage about a different class scores higher on the vector side. A test
builds exactly that case with patched scores and proves the exact figure wins.

**Scope is never widened.** `scope.scoped_documents()` is applied as a database filter first, and
retrieval may only rank what it returned. Passages carry no audience of their own, so a cached or
hand-edited row cannot drift away from its document. A hand-edited public document on a signed-in
shelf is refused by the same filter.

**Refusal.** If the best vector score is below `MIN_SCORE` **and** no keyword token matches, retrieval
returns a refusal with **no sources at all** (so a caller cannot use passages from a refused search),
no chat call is made, and a `KnowledgeGap` with reason `no_match` records the masked question, the
audience shape, the language guess and the top score.

**Citations and the figure check.** Unknown markers such as `[S9]` are stripped; valid ones are kept
and each resolves to a passage id, document id and title. Every number, amount, date part and time
part in the answer must appear in the text of the passages it cited (commas are ignored; numbered
list markers such as `1.` are not figures). A missing valid citation, an empty answer, or any
unmatched figure marks the answer **unverified**. `knowledge/answers.py::deliver()` is the only
function a user-facing screen may use, and it **never returns an unverified answer**: it returns the
fixed refusal instead. The answer desk shows the raw answer, the warning and the exact figure problems
so the admin can see why; the *"what a reader would receive"* panel shows the refusal.

**Honest limit of the check.** It proves that each cited figure exists in the cited text. It does not
prove the sentence around the figure is a fair summary, that a claim without a figure is supported, or
that a true figure was used for the right point. It is a guard against invented money and dates, not a
proof of correctness. K4's question set is what measures correctness.

**Refusal wording.** Fixed English and Kiswahili lines in `answers.py` (`REFUSAL_EN`, `REFUSAL_SW`),
school contact first, both marked **DRAFT** for the school's review; the answer desk displays them.

**Answer desk (admin).** `/assistant/knowledge/answer-desk/`. The admin picks an audience (Visitor,
Parent, Student, Teacher, Finance, Director, Admin), optional class ids and an optional provider, and
asks. The view builds the audience with `scope.audience_for_preview()` — the **same** `Audience`
object a real reader gets — and runs the same `answer_question()` code. There is no admin branch in
visibility or retrieval. Tests prove a Visitor preview never returns a signed-in document, a Parent
of class A never returns class B material, and a Student never returns Finance or Teacher shelves.
Desk asks are `Profile.CONSOLE` usage, and any gap they record is `is_test=True` so it never pollutes
the real gap picture. A safeguarding hit shows the rule, the safe reply and exactly who would be
alerted — and sends **no** alert and **no** email; a test proves no alert row and no notification.

**Per-document check.** On the document page, *"Test this document"* scores that document's own
passages by the same hybrid method. The question embedding is the **only** provider call; no answer is
generated, and the page says so. Disabled until the document is READY, and blocked while its
personal-data scan is unresolved.

**Personal data in documents.** Extraction is followed by a heuristic scan
(`knowledge/personal_data.py`) for emails, Kenyan phones, ID/admission-like numbers and likely person
names next to pupil/parent words. Findings record the kind, heading, passage number and a **masked**
snippet — every other finding in the snippet window is masked too, and the raw value is never stored.
A document with findings cannot be approved or embedded until an admin records the required
confirmation (who, when, and the exact wording); the upload/paste screens carry the warning. The
scan is a heuristic and says so; the confirmation, not the scan, is the decision point. Exactly what
ingestion sends is documented in [security-and-privacy.md](security-and-privacy.md).

**Gaps.** `KnowledgeGap` stores only the masked question, the previewed role, the sorted class ids,
a language hint, the reason, the top score, and — for test tools — `is_test`. No user, IP or session
is stored. The admin can resolve (with a note) or delete any gap. Resolved gaps are proposed for
erasure **90 days after resolution** (`GAP_RETENTION_DAYS = 90`); that period is **DRAFT** and no
scheduled job deletes anything until it is approved.

**Answer cache.** See section 11 for the exact key and read-time re-validation rules.

## 6. Tools (Module 4, designed now)

Read-only tools that wrap existing services (`attendance`, `results`, `finance`, `homework`). A tool
declares its name, required roles, parameter schema and the existing selector it calls.

```
model asks for tool "attendance_summary" (JSON)
  │
  ├─ name must be in the registry
  ├─ arguments validated against the schema (no free-form SQL, no arbitrary user ids)
  ├─ permission check in the service: the asker's identity comes from the session,
  │  never from the model's arguments
  ├─ existing selector returns only the asker's own rows (parent: own children only)
  ├─ result minimised to the facts needed, identifiers stripped
  └─ sensitive reads audit-logged
```

The model never receives a queryset, SQL, or another person's identifiers.

## 7. Guardrails

Order is fixed and enforced in the service, not the prompt.

1. **Safeguarding** — mandatory, runs on every message, cannot be switched off, has no admin setting
   that disables it. Admin may **add** rules; mandatory rules cannot be edited or removed.
2. **Injection defence** — user text and retrieved documents are wrapped in a clearly delimited DATA
   block; the system prompt states that data is never instructions; unknown instruction-like content
   is neutralised rather than obeyed; the system prompt is never echoed back.
3. **PII minimisation** — emails, Kenyan phone numbers, labelled admission/ID numbers, admission-path
   patterns and likely person names are replaced before anything is sent to a provider. This is a
   heuristic, not a named-entity model: it reduces what leaves the server but cannot guarantee
   perfection, and the consent wording says so. Signed-in tools send facts (amounts, dates, statuses),
   not identifiers. What is sent is shown exactly in the admin test console and listed in
   [security-and-privacy.md](security-and-privacy.md).
4. **Output checks** — no system-prompt text, no identifiers outside the asker's scope, no links
   invented, plain-text rendering through the existing sanitiser pattern.

### Safeguarding detection approach (built)

**Method:** a deterministic, versioned rule pack in code (`guardrails/rules.py`), not a provider call.
It works with or without a provider, costs nothing, cannot be silently changed in the database, and is
tested. Text is normalised (curly apostrophes, whitespace), then phrases are matched
case-insensitively on word boundaries. Urgent categories are never suppressed by negation. One
combination rule (secrecy/coercion) only fires alongside another urgent hit.

The mandatory pack has no database rows, so no screen, database restore or admin action can remove
it. Admins may **add** rules (`CustomGuardrailRule`); they can never edit or remove the floor.

**Starter trigger list** (English / Kiswahili / common Sheng; to be reviewed by the school's
safeguarding lead and expanded — Sheng changes fast):

| Category | Starter phrases (indicative) |
|---|---|
| Self-harm, suicide | "kill myself", "end my life", "want to die", "cut myself", "kujiua", "kujidhuru", "nataka kufa", "kujimaliza", "nimechoka na maisha" |
| Physical or sexual abuse | "beats me", "hits me", "touches me", "touches my private parts", "rape", "ananipiga", "ananishika vibaya", "ubakaji", "kubakwa" |
| Neglect, no safe home | "no food at home", "left alone", "sleeping outside", "hakuna chakula", "nimeachwa peke yangu" |
| Threat or weapon | "I will hurt him", "kill them", "bring a knife", "gun", "bomb", "nitamuua", "nitamdhuru" |
| Medical emergency | "can't breathe", "bleeding badly", "overdose", "poisoned", "fire", "accident", "ajali", "moto", "sumu" |
| Coercion or secrecy | "don't tell anyone", "keep this secret", "ananiambia nisimwambie mtu" — only in combination with one of the above |

On a hit: no ordinary answer and no provider call. The user gets a fixed safe reply in English and
Kiswahili whose order is: **the school's named safeguarding contact first, then 999 / 112, then
Childline Kenya 116.** A `SafeguardingAlert` is recorded with a masked excerpt, admin and director
receive an in-app notification with `category="safety"` (quiet hours do not hold it), and the alert
email is sent immediately. The alert stays open until a person acknowledges or closes it.

Lifecycle: `open` → `acknowledged` → `closed`, or `open` → `closed` directly. Closing requires a
recorded outcome, records who closed it and when, and starts the retention clock. Acknowledgement does
not reopen a closed alert. Retention (DRAFT: 12 months after closure) erases only the masked excerpt;
the minimal record stays, and an open alert is never erasable. The purge is a dry-run-by-default
command (`assistant_purge_alert_excerpts`), also usable with `--alert-id` for a deletion request.
Alert rows are not deleted automatically.

The banner and the escalation check share one cached state (60-second TTL): one settings read and one
open-alert query refresh both, so an ordinary admin/director page costs no query while the state is
fresh. Creating, acknowledging, closing an alert or saving settings invalidates it immediately.

**Mandatory before go-live:** a named safeguarding contact (name plus phone or email) is required
before the signed-in assistant or student tutor can be switched on, and a persistent admin dashboard
warning stays visible until it is set. If escalation is not acknowledged within the admin-set window
(default 15 minutes) during working hours, the admin and director are notified again. The check runs
whenever an admin or director loads a dashboard page, so a dead background worker cannot hide an
alert.

### Second-layer detection: designed, not built

Indirect phrasing ("I don't want to be here any more", a child describing a friend, Sheng that
changes monthly) will get past a phrase pack. The designed second layer is an optional model
classifier that runs **after** the deterministic floor and can only add a hit, never remove one.

Cost and privacy implications, recorded so the decision is honest:

- Every screened message would be sent to a provider even when no rule fires. That increases spend
  and expands the data leaving the server; masking would have to be applied first, and a classifier
  reading masked text is weaker.
- It adds latency to every message and a second failure mode: a classifier outage must never block
  the deterministic floor.
- It needs its own evaluation set and a human-reviewed false-positive policy, because a model that
  cries wolf will be ignored by staff.
- It cannot be switched on before the school approves the extra outbound data in the consent wording.

Until that approval, the deterministic floor is the whole system, and this document does not claim
otherwise.

## 8. Handoff

Two sources:
- **Mandatory:** any safeguarding hit (above).
- **Admin-defined:** rules the admin adds (for example "fees dispute", "complaint about a teacher"),
  which the assistant recognises and routes without answering.

A handoff records who asked, when, the trigger, and a short excerpt; notifies admin + director in-app
through the existing `notify_many(..., category="safety")` path (safety bypasses quiet hours), and
emails the admin-set handoff address. The user sees what happens next and how to reach the school.
Handoff is never silent.

## 9. Budgets, limits, kill switch (built)

Enforced in the gateway before any provider call, in this order:

0. **Kill switch** — `AssistantSettings.kill_switch` (default OFF) stops **everything**: the live
   assistant, the admin answer desk, the provider test console and background reading. It is checked
   before every other switch and limit and always wins.
1. **Live switches** — `AssistantSettings.enabled` (default OFF), plus `public_enabled` and
   `signed_in_enabled` (both default OFF). The admin answer desk and the provider console are
   **exempt** from these and from the rate limits below, so an admin can prove a provider and the
   knowledge base before go-live. They are **not** exempt from the kill switch or from their own
   console limits: `console_daily_limit` (default 50 requests/day across both desks) and
   `console_monthly_budget_ksh` (default KSh 500, separate from the site-wide budget). Both are
   admin-editable on the settings screen.
2. **Rate limit** (`limits.py`) — signed-in users: `daily_message_limit` per user per day (default
   20). Public visitors: a **signed cookie** identifies the visitor (10 questions/day/visitor by
   default), the client IP is a backstop (30/day/IP), and a site-wide ceiling caps the whole public
   assistant (300/day). All four numbers are admin-editable. Counting reads `UsageEvent` rows and
   cache counters, so a redeploy cannot reset the day; the day boundary is Nairobi time.
3. **Budget** — month-to-date spend per provider and overall, against the admin's KSh ceiling.
   Estimated worst-case cost of this call is reserved before the call; if it would cross the line the
   call is refused with a plain message. Per-provider cap is optional. Console calls are priced and
   recorded like any other, but counted against the console ceiling rather than the site-wide budget.
4. **Provider circuit state** — an open circuit skips straight to the fallback list.

Costs come from prices the admin enters per provider (USD per million tokens) and a USD→KSh rate in
settings. Every call writes a `UsageEvent` with tokens, estimated cost, latency, provider, model,
outcome and the policy profile; never the key or the message. Usage rows are the ledger budgets read.

The kill switch stops new calls from every path at once; in-flight calls are allowed to finish or time
out. The live switches stop new conversations for their audience while leaving the desks usable.

Why not per-IP alone: on Kenyan mobile networks thousands of people share one carrier NAT address, so
a per-IP-only limit would either turn away a whole school's worth of visitors after 30 questions or be
set so high it protects nothing. The signed cookie gives each visitor a fair share; the IP backstop
stops a single device cycling cookies; the site-wide ceiling and the budget are the real safety net.

## 10. Streaming and perceived speed (proposal — not built)

No streaming code exists. This is the change set that would be proposed for approval before chat
screens ship, because the current web tier cannot hold open streams:

- **Gunicorn** (`Dockerfile:30`): raise workers from 2 to 4 and the timeout from 120 s to 90 s, with
  `--threads 2`, so a few streams cannot occupy every worker.
- **django-q2**: a second `qcluster` with its own queue for assistant ingestion/handoff, so the single
  shared queue and its silent-failure mode stop being load-bearing; plus explicit failure recording
  (the assistant-only `AssistantJobFailure` path already records its own failures).
- **nginx**: keep `proxy_read_timeout 120s`, add `proxy_buffering off` for the chat route only.
- **Cloudflare**: confirm no buffering on the chat route for the tunnel in use.
- **Application**: SSE through `StreamingHttpResponse`, vanilla JS consumer, non-streaming fallback on
  drop; show a "checking school records…" status within 300 ms; cap output at about 400 tokens; stop
  the upstream call when the client disconnects.

In this step the admin test console is short and non-streaming, and the gateway's provider protocol
has no `stream()` method yet. Building it before the worker changes would set up an outage.

## 11. Caching

Uses the existing `CACHES` configuration (`backend/config/settings/base.py:159-169`; Redis in
production, locmem locally).

| Key | Value | TTL |
|---|---|---|
| `assistant:settings` | settings row + provider list | 60 s, invalidated on save |
| `assistant:qembed:<sha256(model signature, masked question)>` | question embedding vector + model + dimension | 30 days |
| `assistant:retrieval:<sha256(audience signature, question, model signature, knowledge version)>` | ranked passage ids with vector, keyword and merged scores; top scores; refusal reason | 10 min |
| `assistant:answer:<sha256(audience signature, visible collection ids, sha256(masked question), model signature, knowledge version)>` | answer text, citation ids/titles, verified/refused flags, top scores, language | 1 hour |
| `assistant:circuit:<provider_id>` | breaker state | 5 min |
| `assistant:rate:<scope>:<id>:<day>` | counters | until end of Nairobi day |

**Cache keys are hashes only.** No question text, role or class list appears in a key.

**A cached answer can never cross an audience.** The audience signature is the role **and** the sorted
class ids; the key also includes the ids of the collections visible to that audience at the time, the
embedding-model signature and a knowledge version counter. A cached answer for class A is therefore
never served to class B or to a different role. Tests cover a different class, a different role and a
changed collection set.

**Effective dates are checked at read time, not left to the TTL.** The chosen rule is: cached rows
store passage **ids and scores**, and every cached id is re-hydrated through the live scope query
(`retrieval.visible_chunk_map`) before it is used. A document that has passed its end date (or was
unapproved, archived or marked stale) is dropped even though the cache still holds its id; if any
cited passage is gone, the whole cached answer is discarded and the question runs again. Cached
retrieval does the same, so an expired passage never influences a ranking. A cached refusal is safe
to reuse because it contains no passages.

**No private data in cache values.** A stored answer holds the answer text, citation labels/ids/titles,
the verified/refused flags, the top scores and a language hint — and nothing about the asker: no user
id, no IP, no session, and **not the question** (typed or masked). Retrieval values hold passage ids
and numbers only. Safeguarding screening runs **before** any cache lookup, so a cached answer can
never bypass it for the individual asker.

## 12. Scaling: what runs where, and when to split

**In the request:** guardrails, limits, retrieval query, provider call, usage write, short replies.

**In the worker:** document extraction, chunking, embedding, re-indexing, handoff email, nightly budget
rollups and retention deletes.

**Today's limits.** django-q2 1.10 runs one `qcluster` with 2 child workers, a single queue, 300 s
timeout, 360 s retry, ORM broker, and no failure acknowledgement (`backend/config/settings/base.py:240-249`;
`compose.yaml:142-151`). Evidence of the failure mode is in the repository: the local worker has not
run since 2026-11-11 (heartbeat row), one queued job has sat undequeued, and zero failures are
recorded. Tasks also swallow their own exceptions, so django-q reports success. The assistant must not
depend on this queue for anything a user waits for; chat is synchronous with a hard timeout.

**Ingestion now uses this queue.** K2 queues a knowledge read through django-q2 after the save
commits, retries a transient failure up to 3 attempts, and records every attempt in
`KnowledgeIngestRun`; a permanent failure writes a plain reason on the document and in
`AssistantJobFailure`. Without a running `qcluster` the document simply stays "waiting" — the
knowledge screens are the visible place to see that — and no user-facing request is affected.
Re-reading is safe: a successful read replaces the document's whole passage set.

**Still planned:** a dedicated assistant queue and worker (a second `qcluster` process) so AI load
cannot starve credential email and digests; explicit acknowledgement at the queue level rather than
each task recording its own failure; idempotent tasks (checksums, "already indexed" guards) because a
redeploy can redeliver a job.

**When this becomes a separate service:** when any of these is true — more than about 20 concurrent
streams, a knowledge base above roughly 100,000 chunks or retrieval p95 above 2 s, more than one
school on the instance, or the web tier needing to scale independently of AI load. The interfaces
keep that option open: providers are a protocol with no Django imports; retrieval is a function
behind a selector; tools are a registry; the chat endpoint is HTTP. Moving any layer behind
HTTP/queue does not change the callers.

## 13. Deployment constraints and the database plan

Verified 2026-11-18:

- **Production is PostgreSQL 17** (`compose.yaml:58`, image `postgres:17-bookworm`), reached through
  `DATABASE_URL` with psycopg2; `ATOMIC_REQUESTS` and persistent connections are on
  (`backend/config/settings/prod.py:17-26`).
- **pgvector is not installed anywhere.** The base image does not ship it, there is no init SQL mount
  and no `CREATE EXTENSION` in any migration. Installing it means replacing the image with
  `pgvector/pgvector:pg17` (same major version, same data directory) and creating the extension once.
  This is a deployment change and is **not** needed for the first retrieval implementation.
- **SQLite fallback:** local development uses `backend/db.sqlite3` with no extensions. JSON embeddings
  plus in-process cosine similarity work identically on SQLite and PostgreSQL; this is why the portable
  approach is recommended first.
- **Host memory is not documented** anywhere in the repository, and Docker sets no memory limits. The
  design therefore uses provider-hosted embeddings and never local model inference.
- The web container is read-only with a 256 MB `/tmp`; new files must live under `/data/media` or the
  private feed root pattern (`backend/config/settings/base.py:185-190`).

## 14. Data model overview

Built (thirteen tables, see [data-model.md](data-model.md)): `AssistantSettings`, `ProviderConfig`,
`UsageEvent`, `CustomGuardrailRule`, `SafeguardingAlert`, `AssistantJobFailure`, plus the four
knowledge catalogue tables `KnowledgeCollection`, `KnowledgeDocument`, `KnowledgeDocumentVersion` and
`KnowledgeQAPair`, the two K2 tables `KnowledgeChunk` (text, heading path, JSON embedding, model,
dimension) and `KnowledgeIngestRun` (one row per attempt), and the K3 table `KnowledgeGap`.

- The **mandatory safeguarding rule pack is not a table** — it lives in
  `guardrails/mandatory.py`, versioned in code, so no admin screen or database edit can weaken it.
  `CustomGuardrailRule` rows are additive only.
- `SafeguardingAlert` stores a masked excerpt, never the full message.
- `AssistantJobFailure` is the assistant-only record of failed background work, visible to admins with
  the reason and retry count.
- Knowledge documents have **no public URL**: files sit outside `MEDIA_ROOT`, the storage `url()`
  raises, and the admin download route is the only read path.
- Audience roles are a canonical pipe-wrapped string so the scope filter is plain SQL on SQLite and
  PostgreSQL alike.
- `KnowledgeGap` holds only the masked question, role, sorted class ids, language hint, reason, top
  score and test flag; no user, IP or session.
- Documents carry a personal-data scan state and the finding locations (kind, heading, passage,
  masked snippet), plus who confirmed, when, and the exact wording they agreed to.

Planned, in order of module:
- **Conversations and handoffs (Module 3):** `Conversation`, `Message`, `HandoffRule`, `Handoff` and
  inbox fields — only after consent wording and retention are approved.

## 15. Security invariants and where they are enforced

| Invariant | Enforced in |
|---|---|
| Keys encrypted, masked, never logged | `crypto.py`, provider services, forms (`render_value=False`), tests asserting absence |
| Tests never call a real provider | `providers/fake.py`; registry refuses real kinds without an adapter |
| Safeguarding cannot be disabled | `guardrails/` mandatory pack; no setting reads it; service always calls it |
| Scope filtering before ranking | `knowledge/scope.py::scoped_documents()` — K3 retrieval calls it and may not re-expand the set; the answer desk builds the reader's `Audience` with the same function |
| Unverified answers never reach a reader | `knowledge/answers.py::deliver()` returns the fixed refusal unless the answer is verified; the chat module must call it, not read `answer.answer` |
| A cached answer cannot outlive its audience or an effective date | Hashed key over role + sorted classes + visible shelves + model + knowledge version; `visible_chunk_map()` re-checks every cached passage at read time |
| Personal data is not embedded without a decision | `personal_data.record_scan()` at extraction; approval blocked while `pii_blocks_embeddings`; `ingest` refuses; confirmation records who/when/wording |
| The kill switch always wins | `gateway.call_chain()` / `embed_chain()` check `kill_switch` before console or live logic |
| Knowledge files are never public | `KnowledgeStorage.url()` raises; the only read route is the admin download view (attachment, `nosniff`, audited) |
| No other person's data | tool permission checks in services; asker identity from session |
| Documents are data | prompt assembly fences SOURCES and the question; injection tests cover a hostile document and a hostile question |
| Sensitive actions audited | `core.audit_log.log_action`, the same path Module 1 already uses |
| New switches default OFF | model defaults and forms |

## 16. Decisions — approved 2026-11-18

The user approved the design with the changes below; Module 1 Step 2 builds to these:

| Decision | Ruling |
|---|---|
| Public identity | signed cookie **and** IP together; per-IP alone is not used (shared carrier NAT) |
| Public limits | 10/day/visitor, 30/day/IP, 300/day site-wide, admin-editable |
| Safeguarding contact | school name/phone/email come **first** in the safe reply, then 999/112, then Childline 116; a named contact is required before signed-in assistant or student tutor can switch on, with a dashboard warning until set |
| Escalation | default 15 min during working hours, re-alerting director and admin by every channel; unacknowledged alerts are checked on any admin/director dashboard load, not only by the worker |
| Detection | deterministic rule pack is the mandatory floor; a model second layer is designed only (cost/privacy in section 7) |
| Outbound masking | names, phones, emails and ID-like numbers are masked before sending; docs state masking is heuristic and show an example |
| Consent | wording is DRAFT with an approval flag, approver name and date; no user-facing screen can show it until approved; retention numbers are proposals |
| Streaming | not built; the proposal in section 10 must be approved first |
| DB privileges | least-privilege Postgres role is a proposal in the runbook, not executed |
| Worker reliability | assistant jobs record failures and are retried with limits; other apps' jobs unchanged |
| Tests/dev | fake provider only; a key-scan test proves no real key-like strings in owned files |

### Module 2 K1 — approved 2026-11-18

| Decision | Ruling |
|---|---|
| Vector storage | Portable JSON float arrays with in-process cosine similarity; pgvector remains a later upgrade and is not installed |
| Audience storage | Canonical pipe-wrapped role strings (`\|parent\|teacher\|`), so the scope filter is ordinary SQL on both SQLite and PostgreSQL |
| Audience ceiling | A document can never be broader than its shelf; enforced in the model, the forms and the services |
| Private storage | `KNOWLEDGE_MEDIA_ROOT` outside `MEDIA_ROOT`; UUID filenames; storage `url()` raises; the admin download view is the only read path |
| Module numbering | Knowledge is **Module 2**; public assistant, handoff rules and human inbox are **Module 3**; signed-in screens and record tools are **Module 4** |

### Module 2 K2 — approved 2026-11-18

| Decision | Ruling |
|---|---|
| PDF reading | **pypdf**; encrypted and scan-only PDFs are refused with plain wording, not guessed at |
| What is indexed | **Approved documents only**, and only while their shelf is approved and inside its effective window. A draft is never read. |
| Web pages | Fetch the page at read time and **keep the text** in `body_text`; only public `http(s)` addresses, redirects re-checked, 10 MB cap |
| Embedding storage | JSON float arrays, as decided in K1; no pgvector, no local model |
| Re-index trigger | Status and shelf saves queue or purge automatically; a provider embedding-model change flags documents **Needs re-index**; a manual action and command are available |
| Ingestion and switches | Ingestion uses the budget but does **not** require the assistant's global switch, so the base can be prepared before go-live |

### Module 2 K3 — approved 2026-11-18

| Decision | Ruling |
|---|---|
| Test place | The admin **answer desk** is where an admin tries real questions and previews each audience |
| Audience preview | The preview calls the **same** scope and retrieval code a real reader uses; no admin bypass or special case. Tested: Visitor never sees signed-in material, Parent of class A never sees class B, Student never sees Finance or Teacher shelves |
| Relevance knobs | `TOP_K`, `MIN_SCORE`, `MAX_QUESTION` are named constants in one place with a comment that scores differ by model; the top score and each source's score are recorded with every answer and gap; K4 calibrates on real embeddings |
| Hybrid search | Vector ranking plus a keyword term-frequency ranking (English/Kiswahili tokenising, case and punctuation insensitive, digit tokens weighted 3×), merged with reciprocal rank fusion; tested that an exact "Grade 4 term fee" passage beats a semantically similar different one |
| Uncited answers | Unknown citation markers stripped; a missing valid citation **or** a figure absent from the cited text marks the answer **unverified**; an unverified answer is never shown to a reader — the fixed refusal is used |
| Gaps | Only role, class signature, language hint, date and the masked question are stored; admin may delete any gap; resolved gaps proposed for erasure after **90 days (DRAFT)** |
| Answer cache | Keys are hashes including role, sorted class ids and visible collection ids; cached passages are re-validated against live scope and effective dates at read time (chosen over short TTLs); values hold no question and no asker data |
| Per-document check | One provider call — the question embedding — and no answer generation; stated on the screen |
| Refusal wording | Fixed English and Kiswahili lines, school contact first, marked **DRAFT** |
| Console exemption | The answer desk and provider console are exempt from the live switches but **not** from the kill switch or their own daily cap (default 50) and monthly ceiling (default KSh 500), both admin-editable. This changed the existing provider console's behaviour: it was previously exempt from budgets |
| Safeguarding in test tools | Show the rule, safe reply and who would be alerted; send **no** real alert or email; mark created gaps as tests |
| Personal data in documents | Scan before embedding, list findings with heading/passage and a masked snippet, require an admin confirmation (who, when, wording) before approval or embedding, warn on the upload screen |
