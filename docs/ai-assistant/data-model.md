# Data model

Module 1 adds three tables in the new `assistant` app. Migration:
`backend/apps/assistant/migrations/0001_initial.py`. No existing table is changed.

Every table inherits `apps.core.models.BaseModel`, which adds `created_at` (indexed) and `updated_at`.

## `assistant_assistant_settings` — one row (pk=1)

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean, default **false** | The school's switch. Off on a fresh install. |
| `default_provider_id` | FK → provider, nullable, `SET_NULL` | Who new conversations will use. |
| `house_rules` | text, blank | Standing instructions (tone, language, never-say list). |
| `monthly_budget_ksh` | decimal(12,2), default 0 | Spending ceiling. 0 = no spending. Not enforced yet. |
| `daily_message_limit` | small positive int, default 20 | Per-person daily cap. 0 = no messages. Not enforced yet. |
| `handoff_email` | email, blank | Where conversations needing a person go. Used from Module 4. |

`AssistantSettings.load()` is `get_or_create(pk=1)`, the same pattern as `SchoolSettings`.

## `assistant_providerconfig` — one row per provider

| Field | Type | Meaning |
|---|---|---|
| `name` | char(100), unique | Admin-facing label. |
| `kind` | char(32) | Adapter to build. Only `fake` exists in this build. |
| `model_name` | char(120), blank | Model identifier for real providers later. |
| `base_url` | URL, blank | Server address for real providers later. |
| `api_key_ciphertext` | text, not editable | Fernet token. Never rendered. |
| `api_key_last4` | char(4), blank | The only readable fragment of the key. |
| `is_active` | boolean, default true | Can be chosen as default / tested. |
| `timeout_seconds` | small positive int, default 30 | Outbound call limit. |

Ordering is by `name`. `has_key` and `masked_key` are properties used by templates.

## `assistant_usageevent` — append-only history

| Field | Type | Meaning |
|---|---|---|
| `provider_id` | FK → provider, nullable, `SET_NULL` | Which provider; kept even if the provider row is removed later. |
| `provider_label` | char(100) | Snapshot of the provider's name at call time. |
| `actor_id` | FK → user, nullable, `SET_NULL` | Who triggered the call (the admin, for a test). |
| `source` | char(20) | `admin_test` today; `chat` when conversations exist. |
| `tokens_in` / `tokens_out` | positive int, default 0 | From the provider's reply. |
| `estimated_cost_ksh` | decimal(10,4), default 0 | Zero for the fake provider. |
| `outcome` | char(10) | `success` or `error`. |
| `detail` | char(200), blank | A short safe reason. Never a key, never a user message. |

Ordering is newest first.

## What is deliberately absent

No message, transcript, document or chunk tables yet. They arrive with the modules that use them
(Modules 3 and 4), each in its own additive migration, so an unbuilt feature has no half-empty table.
