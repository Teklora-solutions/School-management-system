# Architecture

> **Status: approved design, 2026-11-18.** Module 1 Step 2 (the foundation) is built: model gateway,
> provider adapters, budgets, limits, guardrails, alerts, admin screens and the test console. Module 2
> K1 (knowledge data model, private storage and admin screens) is also built. The chat screens,
> reading/indexing, retrieval and record tools are still planned. This document marks **built** and
> **planned** throughout; nothing planned is described as if it exists.

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
| Retrieval | `backend/apps/assistant/knowledge/` + planned `retrieval/` | K1 built: shelves, documents, private files, audience/class scope filter, version history. K2/K3: extraction, chunks, ranking, citations |
| Tools | `backend/apps/assistant/tools/` | read-only record tools wrapping existing services | planned (Module 4) |
| Policies | `backend/apps/assistant/policies.py` | the two assistant profiles | planned (chat modules) |
| Selectors | `backend/apps/assistant/selectors.py` | settings, providers, usage, rules, alerts, failures | built |
| Services | `backend/apps/assistant/services.py` | configuration writes, connection test, test console | built |
| Views | `backend/apps/assistant/views.py` + `urls.py` | admin screens, director alerts, test console | built |
| Tasks | `backend/apps/assistant/tasks.py` | safeguarding email and its limited retry | built |

## 3. Request flow (planned conversation path)

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
           ├─ 3. retrieval.search()           scope filter BEFORE ranking (Module 2 K3)
           │        └─ top N approved chunks with source ids
           │
           ├─ 4. prompt assembly              system rules + house rules + fenced DATA block
           │        └─ PII minimisation applied to everything outbound
           │
           ├─ 5. gateway.chat()               ordered providers, retries, circuit breaker
           │        └─ all down ──► "I can't answer right now…" + school contact
           │
           ├─ 6. output checks                no system prompt echo, no out-of-scope identifiers
           │
           └─ 7. records                      UsageEvent (tokens, cost, latency, outcome);
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

## 5. Knowledge and retrieval (Module 2; K1 built, K2/K3 planned)

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

### 5.2 Planned — reading, ranking and answers

```
question
  │
  ├─ policy scope (public / role / class)  ← applied as a database filter FIRST
  ▼
eligible chunks only
  │
  ├─ embedding of the question (same model the chunks were indexed with)
  ├─ cosine similarity in process (portable) or pgvector (later)
  ▼
top N (6) chunks, de-duplicated, with document title + id
  │
  ▼
prompt: numbered SOURCES block, answer must cite [S1], [S2]
```

Rules:
- **Scope before ranking.** A chunk the asker may not see never enters the candidate set, however
  well it would rank.
- **Embedding model is pinned to the chunk.** Every chunk stores its embedding model and dimension.
  Retrieval only considers chunks embedded with the current model; after a model change the admin is
  warned that re-indexing is required, and stale chunks are ignored rather than compared.
- **Portable storage first.** Embeddings are stored as JSON float arrays in the database. Ranking is
  cosine similarity computed in Python. At school scale (thousands, not millions, of chunks) this is
  one to two seconds at worst and needs no new service, no extension and works on SQLite.
- **pgvector is the upgrade path, not the start.** The selector's signature is
  `search(scope, query_vector, limit)`; a pgvector implementation can replace the body later without
  touching services or views.
- **Citations are mandatory.** If no chunk clears the relevance floor, the assistant says so and does
  not improvise.
- Extraction, chunking (about 800 characters with overlap) and embedding run in the background worker,
  never in a request.

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

Enforced in `gateway.generate()` before any provider call, in this order:

1. **Global switch** — `AssistantSettings.enabled` (default OFF), plus `public_enabled` and
   `signed_in_enabled` (both default OFF). The console profile `UsageEvent.Profile.CONSOLE` is
   exempt so an admin can prove a provider works while the assistant is off.
2. **Rate limit** (`limits.py`) — signed-in users: `daily_message_limit` per user per day (default
   20). Public visitors: a **signed cookie** identifies the visitor (10 questions/day/visitor by
   default), the client IP is a backstop (30/day/IP), and a site-wide ceiling caps the whole public
   assistant (300/day). All four numbers are admin-editable. Counting reads `UsageEvent` rows and
   cache counters, so a redeploy cannot reset the day; the day boundary is Nairobi time.
3. **Budget** — month-to-date spend per provider and overall, against the admin's KSh ceiling.
   Estimated worst-case cost of this call is reserved before the call; if it would cross the line the
   call is refused with a plain message. Per-provider cap is optional.
4. **Provider circuit state** — an open circuit skips straight to the fallback list.

Costs come from prices the admin enters per provider (USD per million tokens) and a USD→KSh rate in
settings. Every call writes a `UsageEvent` with tokens, estimated cost, latency, provider, model,
outcome and the policy profile; never the key or the message. Usage rows are the ledger budgets read.

The global kill switch stops new conversations immediately; in-flight calls are allowed to finish or
time out.

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
| `assistant:embed:<sha256(text)>:<model>` | embedding vector | 30 days |
| `assistant:retrieval:<hash(question, scope, knowledge_version)>` | top chunks | 10 min |
| `assistant:circuit:<provider_id>` | breaker state | 5 min |
| `assistant:rate:<scope>:<id>:<day>` | counters | until end of Nairobi day |
| Public FAQ answer | answer + citations | 1 hour, busted on knowledge change |

Cached answers never bypass guardrails for the individual asker: the input screen runs before the
cache lookup, and the cache key includes the policy profile.

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

**Planned mitigations:** a dedicated assistant queue and worker once knowledge ingestion exists (a
second `qcluster` process); explicit failure recording instead of swallowed exceptions; idempotent
tasks (checksums, "already indexed" guards) because a redeploy can redeliver a job.

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

Built (ten tables, see [data-model.md](data-model.md)): `AssistantSettings`, `ProviderConfig`,
`UsageEvent`, `CustomGuardrailRule`, `SafeguardingAlert`, `AssistantJobFailure`, plus the four
knowledge tables `KnowledgeCollection`, `KnowledgeDocument`, `KnowledgeDocumentVersion` and
`KnowledgeQAPair`.

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

Planned, in order of module:
- **Knowledge chunks (Module 2 K2):** a `KnowledgeChunk` table (text, document, heading/section,
  embedding JSON, embedding model, dimension, checksum, order) plus an ingest-run record for progress
  and retries.
- **Conversations and handoffs (Module 3):** `Conversation`, `Message`, `HandoffRule`, `Handoff` and
  inbox fields — only after consent wording and retention are approved.

## 15. Security invariants and where they are enforced

| Invariant | Enforced in |
|---|---|
| Keys encrypted, masked, never logged | `crypto.py`, provider services, forms (`render_value=False`), tests asserting absence |
| Tests never call a real provider | `providers/fake.py`; registry refuses real kinds without an adapter |
| Safeguarding cannot be disabled | `guardrails/` mandatory pack; no setting reads it; service always calls it |
| Scope filtering before ranking | `knowledge/scope.py::visible_documents()` — built in K1; retrieval must call it and may not re-expand the set |
| Knowledge files are never public | `KnowledgeStorage.url()` raises; the only read route is the admin download view (attachment, `nosniff`, audited) |
| No other person's data | tool permission checks in services; asker identity from session |
| Documents are data | prompt assembly fences DATA; output check for prompt echo |
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
