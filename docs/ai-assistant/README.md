# AI Assistant

One shared assistant engine serving two audiences: visitors on the school website, and signed-in
parents, students, teachers, the director and the admin. The engine is the same; the rules and the
data it can reach differ by role. It answers from approved school documents with citations, fetches
live records only through permission-checked tools, and hands a conversation to a person the admin
chooses when the situation calls for it.

Everything is controlled by the admin: provider, model, keys, knowledge, rules, budgets, handoff.

## Where we are

**Module 1 — Foundations and admin controls — built 2026-11-18.**

| Piece | Status |
|---|---|
| Admin settings screen (`/assistant/settings/`) | ✅ works |
| Provider list, add, edit (`/assistant/providers/`) | ✅ works |
| API keys encrypted at rest, last 4 shown | ✅ works |
| Admin connection test through the gateway | ✅ works (fake provider only) |
| Usage log (`UsageEvent`) | ✅ written by the test button |
| Audit entries for every save | ✅ works |
| Fake provider for tests and local development | ✅ works |
| Real AI provider (OpenAI-compatible, Anthropic, …) | 🚧 not built |
| Knowledge upload and retrieval (RAG) | 🚧 not built |
| Public or signed-in chat screens | 🚧 not built |
| Permission-checked record tools | 🚧 not built |
| Safeguarding detection and handoff | 🚧 not built |
| Budget enforcement | 🚧 fields exist, not enforced |
| Consent notice and retention | 🚧 wording awaiting approval |

**The assistant is switched OFF on a fresh install, and there is no screen yet where anyone can talk
to it.** Module 1 is the safe configuration base everything else plugs into.

## The files

| File | For | What it covers |
|---|---|---|
| [architecture.md](architecture.md) | developer | layers, gateway, planned retrieval/tool/handoff flows |
| [admin-guide.md](admin-guide.md) | school admin | every screen, every setting, step by step |
| [security-and-privacy.md](security-and-privacy.md) | both | key handling, rotation, what leaves the server, consent and retention |
| [data-model.md](data-model.md) | developer | every table and field Module 1 adds |
| [evaluation.md](evaluation.md) | both | test question set (planned) and the current test results |
| [runbook.md](runbook.md) | operator | deploy, back up, restore, rotate keys, provider down |
| [changelog.md](changelog.md) | both | one entry per module |

## Quick rules that do not change

- The assistant switch is **OFF** by default. New switches always are.
- A key is encrypted before it is stored, shown only as its last four characters, and never written
  to a log, audit entry, test fixture or git.
- Tests never call a real provider and never need a real key.
- Safeguarding detection will be mandatory and cannot be switched off (Module 2).
- A user can never retrieve or be told another person's data.
- Documents and user messages are data, never instructions.
