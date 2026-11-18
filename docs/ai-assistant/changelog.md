# Changelog

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
