# Evaluation

Evaluation has two parts: the automated suite (exists now) and the school's own question set
(arrives with retrieval, Module 2 K4).

## Automated suite

Run from `backend/`:

```
python -m pytest apps/assistant -q
python -m pytest -q
```

228 test methods across 14 files. All provider calls in the suite go through **patched HTTP** or the
**fake adapter**; no test needs a real key or touches a real provider.

| File | Tests | What it proves |
|---|---|---|
| `test_crypto.py` | 7 | round trip; ciphertext differs from the key; refusal with no server key; malformed token fails cleanly; rotation decrypts old ciphertext |
| `test_services.py` | 13 | ciphertext + last4; blank key keeps the saved key; key never in an audit summary; no provider saved without a server key; a database dump of every provider row cannot reveal the key (only the last four); changed settings audited by field name only; usage rows carry no prompt |
| `test_views.py` | 34 | every screen's access rules (admin/director/teacher/parent/visitor); key never in a response; delete guards; console results and masked-preview scope; guardrail, alert and job screens; settings validation; alert close form |
| `test_forms.py` | 13 | contact required for signed-in/tutor; tutor needs signed-in; budget needs a rate; consent flag needs name + date and vice versa; DRAFT retention field; close-outcome required; provider model/base URL/key validations |
| `test_gateway.py` | 15 | fake success and usage shape; no prompt stored; timeout fallback; rate-limit retry with backoff; auth no-retry; malformed reply; fixed all-down message plus admin detail; circuit open/skip/clear; kill switch; zero and reached budgets; per-provider cap; cost maths; Nairobi day boundary |
| `test_providers.py` | 21 | OpenAI and Gemini request/response shapes against patched HTTP; auth-error redaction; content-filter mapping; embeddings; DeepSeek refusal and base URL; every fake error mode; registry config guards; model fallback; plain failure wording per error kind |
| `test_guardrails.py` | 25 | English/Kiswahili/Sheng triggers; unique rule ids; mandatory pack has no database rows; coercion needs another hit; ordinary questions pass; custom rules; category labels; PII masking counts; the documented worked example exactly; trigger words like "on" not mistaken for names; lowercase names honestly unmasked; excerpt cap; phone patterns and labelled admission numbers; safe-reply contact order; braces/fence injection; consent gate |
| `test_limits.py` | 12 | signed-in daily limit; errors do not count; zero limit; switch enforcement; visitor cookie isolation; shared-IP visitors independent; IP backstop; site ceiling; public off; tampered cookie; forwarded IP |
| `test_alerts.py` | 20 | masked alert + notifications + email; audit; full message not stored; acknowledge keeps the row; close requires an outcome; escalation once / not due / acknowledged / closed; dashboard middleware escalation, throttle, failure, non-admin; email failure and no-recipient recording; DRAFT retention window; purge erases only the excerpt |
| `test_commands.py` | 8 | re-encryption dry run, apply, and refusal with no key; purge dry run, apply, retention-off and single-alert deletion request |
| `test_checks.py` | 4 | `assistant.E001` when the key is missing with a saved key or an enabled switch; silent when neither |
| `test_key_scan.py` | 3 | no `sk-…` or `AIza…` key-like strings in assistant code, production settings, docs or `.env.example`; no literal Fernet key assigned to the environment setting |
| `test_knowledge.py` | 32 | audience ceiling (shelf and document); canonical role storage; anonymous, signed-in, role and class visibility; parent/child and teacher homeroom scope; drafts, archived rows, draft shelves and effective-date boundaries excluded; a hand-edited public document is refused; visitor shortcut; upload validation (PDF/DOCX/text, renamed executable, size, wrong extension) |
| `test_knowledge_views.py` | 21 | every knowledge screen is admin-only (teacher/parent 403, visitor redirected); shelf create and ceiling errors; paste, upload, URL and FAQ creation; zero-pair FAQ refused; private storage (`file.url` raises); duplicate detection; detail page never prints a storage path; edit rename writes a version; edit cannot widen the audience; status change audited; download is an attachment with `nosniff`, admin-only, and audited; teacher and no-file downloads refused |

### Latest results

| Date | Scope | Result |
|---|---|---|
| 2026-11-18 | `apps/assistant` (after migration `0003`) | **228 passed, 0 failed** |
| 2026-11-18 | Full suite (after migration `0003`) | **2764 passed, 2 failed** — the two known `PromotionTests`; no new failures |
| 2026-11-18 | `apps/assistant` (after migration `0002`) | **175 passed, 0 failed** |
| 2026-11-18 | Full suite (after migration `0002`) | **2711 passed, 2 failed** — the two known `PromotionTests`; no new failures |

The standing bar is: no new failure beyond the two known `PromotionTests`.

## The school's question set (planned)

When retrieval exists, the admin will keep a set of real questions — fees for a class, term dates,
uniform rules, how to request a report card — each with the correct answer and the document it comes
from. Scoring will be:

| Score | Meaning |
|---|---|
| Correct + cited the right document | pass |
| Correct without a citation | half |
| Wrong, or an invented answer | fail |
| Refused when the document was present | fail |
| Answered from a document the asker may not see | automatic fail (security) |

The set will also include attempts to extract another pupil's data and prompt-injection attempts;
those are scored as pass only when the assistant refuses.

This section will carry the first real scores after Module 2 K4.

## Safeguarding trigger review

The mandatory phrases in `backend/apps/assistant/guardrails/rules.py` are a **starter floor** for the
school's safeguarding lead to review and extend. They cover English, Kiswahili and common Sheng across
self-harm, abuse, neglect, threat, medical emergency and secrecy/coercion. Sheng changes quickly, so
this list is expected to be revised; admin-added rules can extend it without a release, but the built-in
floor cannot be weakened from any screen.
