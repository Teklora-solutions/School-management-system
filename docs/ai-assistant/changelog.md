# Changelog

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
