# AI Assistant

One shared assistant engine serving two audiences: visitors on the school website, and signed-in
parents, students, teachers, the director and the admin. The engine is the same; the rules and the
data it can reach differ by role. It answers from approved school documents with citations, fetches
live records only through permission-checked tools, and hands a conversation to a person the admin
chooses when the situation calls for it.

Everything is controlled by the admin: provider, model, keys, knowledge, rules, budgets, handoff.

## Where we are

**Module 1 Step 2 — pipeline foundations — built 2026-11-18, migration `0002` applied.**
**Module 2 K1 — knowledge data model, private storage and admin screens — built 2026-11-18, migration
`0003` applied after review.**

| Piece | Status |
|---|---|
| Admin settings screen (`/assistant/settings/`) | ✅ works |
| Provider list, add, edit, delete (`/assistant/providers/`) | ✅ works |
| API keys encrypted at rest, last 4 shown, rotation command | ✅ works |
| Model gateway: fallback list, timeouts, retries, circuit breaker | ✅ works |
| OpenAI, Gemini, DeepSeek, generic OpenAI-compatible, fake adapters | ✅ works (logic tested against patched HTTP; no real call made) |
| Embeddings in the gateway (OpenAI, Gemini, generic; DeepSeek refused) | ✅ works |
| Admin connection test (one canned prompt, non-streaming) | ✅ works |
| Budget enforcement and the kill switch | ✅ enforced before every provider call |
| Daily limits: signed-in per user; public visitor cookie + IP + site ceiling | ✅ works |
| Mandatory safeguarding rules (EN/SW/Sheng starter pack, in code) | ✅ works |
| Admin-added safeguarding rules (add-only) | ✅ works |
| Safeguarding alerts, masked excerpt, admin/director notification, email | ✅ works |
| Escalation timer, checked on dashboard load as well as by the worker | ✅ works |
| Admin test console with masked-request preview | ✅ works |
| Assistant job failures recorded and shown to admins | ✅ works |
| Consent wording | 🚧 DRAFT, gated behind an approval flag; shown to nobody |
| Knowledge shelves, uploads, audience rules, version history, admin screens | ✅ works (Module 2 K1) |
| Reading files, chunking, embeddings and the ingestion worker | 🚧 Module 2 K2 |
| Retrieval, answers, citations, refusal and the answer cache | 🚧 Module 2 K3 |
| Question set, scoring, gaps report and first results | 🚧 Module 2 K4 |
| Public chat widget and screens | 🚧 Module 3 |
| Handoff rules, safeguarding routing and the human inbox | 🚧 Module 3 |
| Signed-in chat screens and permission-checked record tools | 🚧 Module 4 |
| Streaming replies | 🚧 proposal in [architecture.md](architecture.md#10-streaming-and-perceived-speed-proposal--not-built) needs approval |
| Model second-layer safeguarding detection | 🚧 designed only, not built |

**The assistant is switched OFF on a fresh install, and there is still no screen where a parent,
student, teacher or visitor can talk to it.** What exists is the whole machinery behind that screen,
tested end to end through the admin console and the fake provider, plus the knowledge shelves an
admin fills with approved wording. Nothing reads those documents yet: reading, search and answers are
K2–K4.

## The files

| File | For | What it covers |
|---|---|---|
| [architecture.md](architecture.md) | developer | layers, gateway, budgets/limits, guardrails, built knowledge storage, planned retrieval/tool/handoff flows |
| [admin-guide.md](admin-guide.md) | school admin | every screen, every setting, step by step |
| [security-and-privacy.md](security-and-privacy.md) | both | key handling, rotation, what leaves the server, consent and retention |
| [data-model.md](data-model.md) | developer | every table and field |
| [evaluation.md](evaluation.md) | both | safeguarding trigger set, test inventory, known gaps |
| [runbook.md](runbook.md) | operator | deploy, back up, restore, rotate keys, provider down, least-privilege DB plan |
| [changelog.md](changelog.md) | both | one entry per module |

## Quick rules that do not change

- Every new switch is **OFF** by default — the master switch, public, signed-in, student tutor.
- A key is encrypted before it is stored, shown only as its last four characters, and never written
  to a log, audit entry, test fixture or git.
- Tests never call a real provider and never need a real key; a test scans the assistant's files for
  key-like strings.
- Safeguarding detection is mandatory and cannot be switched off; admin rules can only add to it.
- A user can never retrieve or be told another person's data. Audience and class are filtered **before**
  any ranking, and a document can never be wider than its shelf.
- Knowledge files live outside `MEDIA_ROOT`, are never given a public URL, and are served only through
  the permission-checked admin download, as an attachment.
- Documents and user messages are data, never instructions.
- The consent wording is a draft and is gated behind an explicit approval flag with a name and date.
