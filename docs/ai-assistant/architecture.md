# Architecture

## Layers

The app follows the same rule as the rest of the repository: reads through selectors, writes through
services, permissions in the view and the service.

| Layer | Files | Job |
|---|---|---|
| Models | `backend/apps/assistant/models.py` | three tables: settings, providers, usage |
| Providers | `backend/apps/assistant/providers/` | framework-free adapters. `base.py` defines the interface; `fake.py` is the only adapter in this build; `registry.py` maps a kind string to an adapter |
| Crypto | `backend/apps/assistant/crypto.py` | Fernet encryption of provider keys; no logging anywhere in the file |
| Selectors | `backend/apps/assistant/selectors.py` | the singleton, provider list, active provider, recent usage |
| Services | `backend/apps/assistant/services.py` | all writes, audit entries, usage records, the connection test |
| Views | `backend/apps/assistant/views.py` | admin-only screens; permissions via `AdminRequiredMixin` |
| Templates | `frontend/templates/features/assistant/` | settings, provider list, provider form |

## What runs today

```
Admin browser
  │
  ├─ GET/POST /assistant/settings/ ─────────► AssistantSettingsView
  │                                              │ form_valid
  │                                              ▼
  │                                        services.update_assistant_settings
  │                                              │
  │                                              ├─ save singleton (pk=1)
  │                                              └─ core.audit_log.log_action (field names only)
  │
  ├─ GET/POST /assistant/providers/... ────► Provider views
  │                                              │
  │                                              ▼
  │                                        services.create_provider / update_provider
  │                                              │
  │                                              ├─ crypto.encrypt(key) ──► Fernet, key from env
  │                                              ├─ store ciphertext + last4
  │                                              └─ audit (key_set / key_updated, never the key)
  │
  └─ POST /assistant/providers/<pk>/test/ ──► services.test_provider
                                                 │
                                                 ▼
                                           providers.registry.build_provider(kind)
                                                 │
                                                 ▼
                                           FakeProvider.generate()   (in-process, no network)
                                                 │
                                                 ├─ UsageEvent (tokens, outcome)
                                                 └─ audit assistant.provider_tested
```

Nothing in this build makes an outbound network call. `registry.build_provider` refuses every kind
except `fake` with a clear message, and the provider form only offers `fake`.

## Key handling

```
ASSISTANT_ENCRYPTION_KEY (environment, comma-separated, newest first)
        │
        ├─ encrypt → first key only
        └─ decrypt → each key in turn (rotation without downtime)

database: api_key_ciphertext (opaque token) + api_key_last4 (four characters)
screens:  "•••• wxyz"
```

If the environment variable is absent, the screens still load and settings still save; saving an API
key is refused with an explanation. The test suite supplies a fixed throwaway key in
`config/settings/test.py`.

## Planned flows (not built yet)

These are recorded so later modules can be checked against the intended shape. None of this code
exists today.

### Retrieval (Module 3)

```
question ──► role + class scope (BEFORE ranking)
              │
              ▼
        candidate chunks (only documents the role may see)
              │
              ▼
        ranking ──► top N chunks ──► prompt with citations
```

Filtering before ranking is the rule: a student's question must never be answered from a document
that student could not open, even if the chunk ranks highest.

### Tools (Module 5)

Read-only tools that wrap existing services:

```
tool call ──► permission check (role, and the student/class relationship)
           ──► existing selector (attendance / results / finance / homework)
           ──► minimised result (no identifiers beyond the asker's scope)
           ──► audit if the action is sensitive
```

No tool returns another person's rows. The model chooses a tool by name; it never receives SQL and
never receives a queryset.

### Handoff and safeguarding (Modules 2, 4)

```
every message ──► safeguarding check (cannot be disabled)
                      │
                      ├─ hit ──► escalation to the admin-defined contact; conversation flagged
                      │          (never answered as ordinary school business)
                      └─ clear ──► normal path
```

Handoff uses the admin's handoff email. Safeguarding escalation is mandatory; the admin can change
who receives it, not whether it happens.

## Scaling notes

- Provider calls are synchronous today only because there are none. Before real providers arrive, the
  call moves behind the existing Q2 task queue for anything long-running; the screens keep to
  short-lived request/response.
- Usage rows are append-only and indexed by creation time through `BaseModel`; budget queries will
  aggregate by month.
- Chunk storage (Module 3) will live outside `MEDIA_ROOT`, like the class-feed files, so no raw
  document is publicly served.
