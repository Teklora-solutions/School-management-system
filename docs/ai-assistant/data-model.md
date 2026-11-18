# Data model

The `assistant` app owns ten tables. Migration `0001_initial` created the first three; migration
`0002` (applied locally on 2026-11-18) adds the fields and tables introduced by Module 1 Step 2;
migration `0003_knowledge_base` (applied locally on 2026-11-18, after `sqlmigrate` review) adds the
four knowledge tables in Module 2 K1. No existing table outside the app is changed.

Every table inherits `apps.core.models.BaseModel`, which adds `created_at` (indexed) and `updated_at`.

## `assistant_assistantsettings` — one row (pk=1)

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean, default **false** | Global kill switch. |
| `public_enabled` | boolean, default **false** | Website assistant for visitors. |
| `signed_in_enabled` | boolean, default **false** | Assistant for signed-in users. |
| `student_tutor_enabled` | boolean, default **false** | Tutor mode; needs the signed-in switch and a contact. |
| `default_provider_id` | FK → provider, nullable, `SET_NULL` | Tried first; the rest follow fallback order. |
| `house_rules` | text, blank | Admin's standing instructions. |
| `monthly_budget_ksh` | decimal(12,2), default 0 | Site-wide monthly ceiling. 0 = no spending. |
| `usd_to_ksh` | decimal(10,2), default 0 | Converts provider USD prices to the budget. |
| `daily_message_limit` | positive small int, default 20 | Signed-in messages per user per day. |
| `public_visitor_daily_limit` | positive small int, default 10 | Questions per visitor per day (signed cookie). |
| `public_ip_daily_limit` | positive small int, default 30 | Questions per IP per day (backstop). |
| `public_site_daily_limit` | positive int, default 300 | Questions across the whole public assistant per day. |
| `safeguarding_contact_name` | char(120), blank | Named person in the safe reply. |
| `safeguarding_contact_phone` | char(40), blank | Contact phone. |
| `safeguarding_contact_email` | email, blank | Contact email. |
| `escalation_minutes` | positive small int, default 15 | Unacknowledged-alert timer. |
| `working_hours_start` / `working_hours_end` | time, default 07:00 / 17:00 | Escalation clock window; equal values = 24 hours. |
| `alert_retention_months` | positive small int, default 12 | **DRAFT.** Months after an alert is closed before its excerpt is erased. 0 = keep excerpts. |
| `handoff_email` | email, blank | Where handoff and alert emails go. |
| `consent_approved` | boolean, default **false** | Approval gate for the draft consent wording. |
| `consent_approved_by` | char(120), blank | Approver's name. |
| `consent_approved_on` | date, nullable | Approval date. |

Properties: `has_safeguarding_contact` (name plus a phone or email), `consent_ready` (flag **and**
name **and** date), `within_working_hours(moment)`. `load()` is `get_or_create(pk=1)`.

## `assistant_providerconfig` — one row per provider

| Field | Type | Meaning |
|---|---|---|
| `name` | char(100), unique | Admin-facing label. |
| `kind` | char(32) | `fake`, `openai`, `deepseek`, `gemini`, `openai_compatible`. |
| `model_name` | char(120), blank | Main chat model. |
| `fast_model_name` | char(120), blank | Cheaper model for simple public questions. |
| `embedding_model_name` | char(120), blank | Embedding model; changing it means re-indexing. |
| `base_url` | URL, blank | Required for OpenAI-compatible servers. |
| `api_key_ciphertext` | text, not editable | Fernet token. Never rendered. |
| `api_key_last4` | char(4), blank | The only readable fragment of the key. |
| `is_active` | boolean, default true | Usable and testable; inactive rows are skipped. |
| `timeout_seconds` | positive small int, default 30 | Outbound call limit. |
| `priority` | positive small int, default 100 | Fallback order; lower is earlier. |
| `input_usd_per_1m` | decimal(10,4), default 0 | Price per 1M input tokens. |
| `output_usd_per_1m` | decimal(10,4), default 0 | Price per 1M output tokens. |
| `embedding_usd_per_1m` | decimal(10,4), default 0 | Price per 1M embedding tokens. |
| `monthly_budget_ksh` | decimal(12,2), nullable | Optional per-provider cap. |
| `region_note` | char(200), blank | Where the provider processes data, as a reminder. |

Ordering is `priority, name`. `has_key` and `masked_key` are properties used by templates.

## `assistant_usageevent` — append-only ledger

| Field | Type | Meaning |
|---|---|---|
| `provider_id` | FK → provider, nullable, `SET_NULL` | Which provider; snapshot kept in `provider_label`. |
| `provider_label` | char(100) | Provider name at call time. |
| `actor_id` | FK → user, nullable, `SET_NULL` | Who triggered the call. |
| `source` | char(20) | `admin_test` or `chat`. |
| `profile` | char(20) | `console`, `public` or `signed_in`. |
| `model_name` | char(120), blank | Model used for this attempt. |
| `tokens_in` / `tokens_out` | positive int, default 0 | From the provider's reply. |
| `estimated_cost_ksh` | decimal(10,4), default 0 | Price × tokens × USD→KSh rate. |
| `latency_ms` | positive int, default 0 | Wall-clock time for the attempt. |
| `outcome` | char(10) | `success`, `error`, `blocked`. |
| `detail` | char(200), blank | A short safe reason. Never a key, never a user message. |

One row is written **per attempt**, so a fallback chain that tries two providers leaves two rows. The
budget and rate-limit checks read these rows.

## `assistant_customguardrailrule` — admin-added rules

| Field | Type | Meaning |
|---|---|---|
| `name` | char(120) | Rule label. |
| `category` | char(20) | Same category keys as the mandatory pack. |
| `patterns` | text | One phrase per line; matched on word boundaries. |
| `note` | text, blank | Why the rule exists. |
| `is_active` | boolean, default true | Switched-off rules keep their row for history. |

The **mandatory rules are not in this table**. They live in `guardrails/rules.py`, so no database edit
or restore can remove them.

## `assistant_safeguardingalert` — one row per hit

| Field | Type | Meaning |
|---|---|---|
| `profile` | char(20) | Which assistant profile saw it. |
| `category` | char(40) | Category key. |
| `rule_id` | char(80), blank | Which rule fired (`M-…` or `CUSTOM-<pk>`). |
| `excerpt` | char(300), blank | **Masked** excerpt only, never the full message. |
| `user_id` | FK → user, nullable, `SET_NULL` | Signed-in asker, if any. |
| `ip_address` | generic IP, nullable | Visitor address, if public. |
| `status` | char(20) | `open`, `acknowledged` or `closed`. |
| `acknowledged_at` / `acknowledged_by_id` | datetime/FK, nullable | Who took it on. |
| `escalated_at` | datetime, nullable | When the overdue re-notification went out. |
| `closed_at` / `closed_by_id` | datetime/FK, nullable | When and by whom the alert was closed. |
| `outcome` | text, blank | Staff notes: who was contacted, what was done. Not copied to the audit summary. |
| `excerpt_erased_at` | datetime, nullable | Set when retention (or a deletion request) erased the excerpt. |

Rows are never deleted automatically. A closed alert's **excerpt** may be erased after
`alert_retention_months` (DRAFT) or early for a deletion request; the minimal record — category,
profile, dates, outcome, who closed it — stays. Open alerts are never erasable. The outcome text is
staff notes and is not erased by the command.

## `assistant_assistantjobfailure` — assistant background failures

| Field | Type | Meaning |
|---|---|---|
| `task_name` | char(120) | Which job. |
| `reason` | char(300) | Safe failure reason. |
| `attempts` | positive small int, default 1 | How many times it was tried. |
| `resolved_at` | datetime, nullable | Set when an admin marks it resolved. |

Only assistant jobs write here; other apps' jobs keep their existing behaviour.

## `assistant_knowledgecollection` — one row per shelf

| Field | Type | Meaning |
|---|---|---|
| `name` | char(120), unique | Admin-facing shelf name, e.g. Admissions, Fees, Parent Guide. |
| `slug` | slug(140), unique, blank | Made from the name when left blank; collisions get `-2`, `-3`. |
| `description` | text, blank | What belongs on this shelf. |
| `audience` | char(20) | `public` or `signed_in`. The **widest** audience any document here may have. |
| `roles` | char(200), blank | Canonical role string, e.g. `\|parent\|teacher\|`. Empty means every signed-in role. A public shelf must keep this empty. |
| `status` | char(20) | `draft`, `approved` or `archived`. Draft and archived shelves are never used for answers. |
| `effective_from` / `effective_to` | date, nullable | Optional window. Checked by day in Nairobi time when documents are selected. |
| `owner_id` | FK → user, nullable, `SET_NULL` | The accountable person; filled with the creator when blank. |
| `classes` | M2M → `academics.SchoolClass`, blank | Optional class limit for the shelf. Empty means every class. |

Indexes: `(status, audience)`. Ordering: name.

## `assistant_knowledgedocument` — one row per source

| Field | Type | Meaning |
|---|---|---|
| `collection_id` | FK → collection, `PROTECT` | The shelf. Deleting a shelf that still has documents is refused. |
| `title` | char(200) | Shown in citations; make it recognisable. |
| `source_type` | char(12) | `pdf`, `docx`, `txt`, `url`, `pasted` or `faq`. |
| `file` | file, blank | Uploaded PDF/DOCX/TXT/MD. Stored under `KNOWLEDGE_MEDIA_ROOT` with a UUID filename. `url()` raises. |
| `source_url` | URL, blank | The web page address for `url` documents. Nothing is fetched yet. |
| `body_text` | text, blank | Pasted wording, or the introduction of an FAQ document. |
| `language` | char(8) | `en` or `sw`. |
| `audience` | char(20) | `public` or `signed_in`; must be narrower than or equal to the shelf. |
| `roles` | char(200), blank | Canonical role string as on the shelf; must be within the shelf's roles. Empty = every signed-in role. |
| `classes` | M2M → class, blank | Optional class limit; must be within the shelf's classes. Empty = every class. |
| `status` | char(20) | `draft`, `approved` or `archived`. Only approved documents are ever used. |
| `effective_from` / `effective_to` | date, nullable | Same day-level check as the shelf. |
| `owner_id` | FK → user, nullable, `SET_NULL` | Creator or accountable person. |
| `version` | positive int, default 1 | Current version number; every metadata save that changes the title writes a history row. |
| `content_sha256` | char(64), not editable | Fingerprint of pasted/FAQ text or the URL. Used for duplicate detection. |
| `file_sha256` | char(64), not editable | Fingerprint of the uploaded bytes. Used for duplicate detection. |
| `duplicate_of_id` | FK → document, nullable, `SET_NULL` | An earlier document with the same file or content; shown as a warning, never merged automatically. |
| `is_stale` | boolean, default false | Set when the embedding model changes; a stale document is ignored until re-indexed (K2). |
| `ingestion_state` | char(12) | `pending`, `running`, `ready`, `failed` or `skipped`. Idle until K2 runs. |
| `indexed_model` | char(120), blank | Model that produced the stored chunks (K2). |
| `indexed_at` | datetime, nullable | When indexing last succeeded (K2). |
| `chunk_count` | positive int, default 0 | Chunks stored for this document (K2). |
| `last_error` | char(300), blank | Last ingestion failure in plain words (K2). |

Indexes: `(status, audience)`, `(collection, status)`, `content_sha256`, `file_sha256`,
`ingestion_state`. Ordering: collection, title.

**How audience is stored.** Roles are a canonical pipe-wrapped string (`|parent|teacher|`), not a JSON
array. Both SQLite and PostgreSQL support `roles__contains="|parent|"`, so the same filter runs on
development and production. The selector `knowledge/scope.py::visible_documents(user, on_date)` builds
the database filter **first**; ranking (K3) receives only the rows that survived it.

**The audience ceiling.** A document may be narrower than its shelf, never broader. This is checked in
`KnowledgeDocument.clean()`, again in the upload/edit forms so the error lands on the right field, and
again in `knowledge/services.py` before anything is written. Tests include a hand-edited row that tries
to leak a public document through a signed-in shelf; the selector refuses it.

## `assistant_knowledgedocumentversion` — history

| Field | Type | Meaning |
|---|---|---|
| `document_id` | FK → document, `CASCADE` | The document. |
| `version` | positive int | Version number; unique with the document. |
| `title` | char(200) | Title at that version. |
| `note` | char(200), blank | Why it was saved (the edit form's "change note"). |
| `content` | text, blank | Pasted text at that version; blank for file and URL documents. |
| `source_url` | URL, blank | Address at that version. |
| `file_sha256` | char(64), blank | File fingerprint at that version. |
| `created_by_id` | FK → user, nullable, `SET_NULL` | Who saved it. |

Ordering: newest first. `unique_together = (document, version)`.

## `assistant_knowledgeqapair` — question and answer rows

| Field | Type | Meaning |
|---|---|---|
| `document_id` | FK → document, `CASCADE` | The FAQ document these belong to. |
| `question` | char(300) | The question, as a parent or pupil would ask it. |
| `answer` | text | The exact approved answer. |
| `display_order` | positive small int, default 0 | Order on the detail screen and in prompts. |
| `is_active` | boolean, default true | Inactive pairs stay for history but are not used. |

Ordering: `display_order`, then pk.

## What is deliberately absent

- **Chunks and embeddings (Module 2 K2):** no chunk table and no vector column yet. The documents'
  `ingestion_*` columns are present and idle; they describe work K2 will do. Nothing is compared or
  scored today.
- **Conversations and handoffs (Module 3):** no message, transcript, conversation or handoff table.
  They arrive with the public widget and the human inbox, after consent wording and retention are
  approved.

Each arrives in its own additive migration, so an unbuilt feature has no half-empty table.
