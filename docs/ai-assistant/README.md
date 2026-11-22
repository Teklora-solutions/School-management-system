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
**Module 2 K2 — reading, chunking, embeddings and the ingestion worker — built 2026-11-18, migration
`0004` applied.**
**Module 2 K3 — hybrid retrieval, answers, citations, the answer desk and gaps — built 2026-11-18,
migration `0005` applied (see the caveat in [changelog.md](changelog.md)).**
**Module 3 CP1 — handoff foundations — built 2026-11-18, migration `0006` applied.**
**Module 3 CP2 — public chat backend — built 2026-11-18, no migration.**
**Module 3 CP3 — public chat widget, mounted once from `public/base.html` — built 2026-11-18, no
migration. Switched off by default, so a fresh install renders nothing.**

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
| Admin test console with masked-request preview | ✅ works (exempt from the live switches, subject to its own cap, ceiling and the kill switch) |
| Assistant job failures recorded and shown to admins | ✅ works |
| Consent wording | 🚧 DRAFT, gated behind an approval flag; shown to nobody |
| Knowledge shelves, uploads, audience rules, version history, admin screens | ✅ works (Module 2 K1) |
| Reading files, web pages and FAQs; chunking; embeddings; ingestion worker | ✅ works (Module 2 K2; needs `qcluster`) |
| Personal-data scan and admin confirmation before any passage is embedded | ✅ works (K3) |
| Re-index screen, stale detection and the `reindex_knowledge` command | ✅ works (Module 2 K2) |
| Hybrid retrieval (vector + keyword, merged), scope filter first, refusal | ✅ works (K3) |
| Answers with citations, figure check, unverified handling and answer cache | ✅ works (K3) |
| Answer desk: ask as Visitor/Parent/Student/Teacher… with true visibility | ✅ works (K3) |
| Per-document check ("Try a question against this document") | ✅ works (K3) |
| Knowledge gap list, resolve and delete | ✅ works (K3) |
| Question set, scoring and first results | 🚧 Module 2 K4 |
| Public chat backend: state, send, new, feedback, callback; per-visitor/IP/site limits; PoW; consent gate | ✅ works (Module 3 CP2) |
| Public chat widget on every public page (phone-first, EN/SW, streaming, feedback, callback) | ✅ works (Module 3 CP3); OFF by default, ships nothing while off |
| Handoff rules, safeguarding routing and the human inbox | 🚧 Module 3 CP4–CP5 |
| Signed-in chat screens and permission-checked record tools | ✅ built (Module 4): chat page + floating button for every role, 33 role-scoped tools, model-selected calls with keyword fallback, tutor mode with graded-work refusal, confirm-to-save announcement drafts, memory controls, EN/SW toggle |
| Streaming replies | ✅ built (Module 3 CP2–CP3): SSE, only after the whole answer is verified |
| Model second-layer safeguarding detection | 🚧 designed only, not built |

**The assistant is switched OFF on a fresh install.** A visitor now has a screen to talk to it — the
public chat widget — but only after an admin turns the public switch on and approves the consent
wording; while it is off, public pages ship no widget markup, styles or script at all. There is still
no signed-in chat for parents, students or teachers. What exists is the whole machinery behind the
widget, tested end to end through the admin answer desk, the test console, the fake provider and a real
browser check on a throwaway copy, plus the knowledge shelves an admin fills with approved wording. Approved documents are read in the background
and stored as embedded passages; the answer desk now retrieves from them with hybrid search, asks the
model for a cited answer, checks the citations and figures, records a gap when nothing reliable is
found, and shows exactly what a real reader in the previewed role would receive. The school's question
set and the first real scores are K4.

## The files

| File | For | What it covers |
|---|---|---|
| [architecture.md](architecture.md) | developer | layers, gateway, budgets/limits, guardrails, built knowledge storage, hybrid retrieval and the answer pipeline, planned tool/handoff flows |
| [admin-guide.md](admin-guide.md) | school admin | every screen, every setting, step by step |
| [security-and-privacy.md](security-and-privacy.md) | both | key handling, rotation, what leaves the server, consent and retention |
| [data-model.md](data-model.md) | developer | every table and field |
| [evaluation.md](evaluation.md) | both | safeguarding trigger set, test inventory, known gaps |
| [runbook.md](runbook.md) | operator | deploy, back up, restore, rotate keys, provider down, least-privilege DB plan |
| [changelog.md](changelog.md) | both | one entry per module |

## Quick rules that do not change

- Every new switch is **OFF** by default — the master switch, public, signed-in, student tutor. The
  **kill switch** is the only control that always wins: it stops the answer desk, the test console and
  background reading as well as live chat.
- A key is encrypted before it is stored, shown only as its last four characters, and never written
  to a log, audit entry, test fixture or git.
- Tests never call a real provider and never need a real key; a test scans the assistant's files for
  key-like strings.
- Safeguarding detection is mandatory and cannot be switched off; admin rules can only add to it.
- A user can never retrieve or be told another person's data. Audience and class are filtered **before**
  any ranking, and a document can never be wider than its shelf. The answer desk preview builds the
  same audience object a real reader gets; there is no admin shortcut.
- An answer with no valid citation, or with a figure that does not appear in its cited passages, is
  **unverified** and is never shown to a reader: the fixed refusal is shown instead.
- Knowledge files live outside `MEDIA_ROOT`, are never given a public URL, and are served only through
  the permission-checked admin download, as an attachment.
- Reading runs only in the background worker, and only for approved documents on approved, effective
  shelves. A document whose scan found personal data cannot be approved or embedded until an admin
  confirms it, and withdrawing approval removes the stored passages immediately.
- Embedding a document sends its **passage text** (not the title, file name or metadata) to the
  configured embedding provider. A web page is fetched only over public http(s) addresses; the fetched
  text is kept so an admin can see what was read.
- Cached answers never outlive their audience: the key includes the role, the sorted class ids and the
  visible shelves, and every cached citation is re-checked against live scope and effective dates at
  read time.
- Documents and user messages are data, never instructions.
- The consent wording, the refusal wording and the proposed retention periods are drafts gated behind
  explicit approval.
