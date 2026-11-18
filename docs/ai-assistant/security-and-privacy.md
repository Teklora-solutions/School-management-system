# Security and privacy

## API keys

### How a key is held

- The key is encrypted with **Fernet** (AES-128-CBC + HMAC, from the `cryptography` package) before
  it touches the database. What is stored is an opaque token plus the last four characters.
- The encryption key itself is **not** in the database, not in git, and not in any database backup. It
  lives in the server environment as `ASSISTANT_ENCRYPTION_KEY`
  (`backend/config/settings/base.py`), read from the process environment or the repo-root `.env`
  (which is not committed), and can be generated with `python manage.py assistant_make_key`.
- **There is no weak default and no plaintext fallback.** If the variable is missing:
  - saving a real provider key through the form is refused;
  - reading a saved key raises a `CONFIG` error naming the environment variable;
  - a Django system check (`assistant.E001`) fails whenever a saved provider key exists or the
    assistant is enabled, so `manage.py check`, `runserver` and `migrate` stop and print why. A bare
    `gunicorn` process does not run Django checks, but the production `app-init` runs `migrate`, so
    the compose stack fails to start rather than serve without a key.
  - The check is silent on a fresh install with no saved key and the assistant off, so the rest of the
    site installs normally first.
- Production must **forward** the variable into the app container. `compose.yaml` does not yet pass it
  through; that one-line addition is required before a real key is entered (see Known gaps).
- Screens show `•••• 1234` and nothing else. Forms never re-render a saved key
  (`render_value=False`). The audit trail records that a key was set or replaced (`key_set`,
  `key_updated`, `key_last4`) and never the value.
- Provider error messages are passed through `redact()`, which replaces the key with `[key]` and caps
  the length, before they can reach a message, a usage row or a log.
- `apps/assistant/crypto.py` contains no logging. A test scans the assistant's code, production
  settings, docs and `.env.example` for key-like strings and fails if one is found. The test settings
  generate a throwaway Fernet key per test process at runtime, so no key literal exists in a test
  file either.
- The Django admin site does not register these models, so keys are not browsable there.

### Rotation

The environment variable accepts a comma-separated list, newest first:

```
ASSISTANT_ENCRYPTION_KEY=NEW_KEY,OLD_KEY
```

Encryption always uses the first key; decryption tries each key in turn. Sequence:

1. Generate a new key: `python manage.py assistant_make_key`.
2. Put it **first**, keep the old one after the comma, restart the app.
3. Either re-save each provider's key through the edit form, or run the batch command:

   ```
   cd backend
   python manage.py assistant_reencrypt_keys            # dry run: reports what would change
   python manage.py assistant_reencrypt_keys --apply    # re-encrypts everything with the first key
   ```

   The command never prints a key. It refuses to run if no key is configured, and leaves a row
   untouched if the current key set cannot decrypt it.
4. Remove the old key from the environment and restart.

Skipping step 3 means rows stay encrypted under the old key, and removing it in step 4 makes them
unreadable. The command exists so a rotation does not depend on remembering each key.

### What to back up

The database **and** the encryption key, separately. A database backup without the key cannot reveal
saved provider keys; a key without the database is useless. Never put the key inside a database backup
or a ticket.

This is proved by test, not just asserted: `test_a_database_dump_alone_cannot_reveal_the_key` dumps
every `ProviderConfig` row, confirms the API key is absent and only the last four characters appear,
then removes `ASSISTANT_ENCRYPTION_KEY` and shows that the stored ciphertext raises
`EncryptionUnavailable` instead of decrypting. A second test shows the runtime says which environment
variable is missing rather than producing a broken call.

## What leaves the system

### Today

- What the **admin** sends from the **test console**, the **answer desk** or a provider's **Test**
  button, to the provider the admin configured.
- **Approved knowledge documents, when they are embedded.** From K2, the text of each passage is sent
  to the configured embedding provider. This is school-approved wording, not pupil records, but it is
  document text leaving the server and the admin should know it: a document marked Private for staff
  is still embedded by a third-party provider. Embedding is skipped entirely while the fake provider
  is the only one configured; the fake adapter runs in-process.
- **The answer desk's question embedding and answer call.** The masked question is embedded, the top
  passages and the masked question go to the chat provider, and the answer comes back. The desk is
  admin-only and its usage is `source=admin_test`.
- **The per-document check's question embedding.** One embedding call; no answer is generated.
- **A visitor's question from the public widget**, from CP2, once the public switch is on and the
  consent wording is approved: the masked question (plus the Public shelves' passages) goes to the
  chat provider, and the question is embedded first when a real embedding model is configured. The
  stored message is the masked text. Honeypot and proof-of-work refusals never reach a provider, and a
  safeguarding hit returns only the fixed safe reply.
- No parent, student or teacher message leaves the server, because the signed-in chat is Module 4.

If the configured provider is the fake one, nothing leaves the server at all.

### Exactly what ingestion sends

Embedding a document sends, per passage, **only the passage text** and the embedding model name.
Titles are not sent. File names, the storage path, the shelf, audience, roles, class links, status,
effective dates, owner, fingerprints and version history are **not** sent. The system prompt and
house rules are not sent on an embedding call. What is sent is the extracted text, split into passages
of about 800 characters (with heading paths kept as part of the passage text where chunking produced
them), batched 32 passages per request.

**Documents are not masked before embedding.** Masking school wording would corrupt it and make it
unsearchable. Since K3 the rule is enforced by a gate instead:

1. **Scan.** Before anything is embedded, each document's passages are scanned
   (`knowledge/personal_data.py`) for emails, Kenyan phone numbers, ID/admission-like numbers and
   likely person names next to pupil/parent/guardian words (including `mwanafunzi`, `mzazi`, `mtoto`).
   Findings are listed on the document page by kind, heading and passage number, with a **masked**
   snippet. The scan is a heuristic: unusual spellings, Sheng nicknames and names with no trigger
   word can be missed, and a harmless number can be flagged.
2. **Stop.** A document with findings is **not embedded and cannot be approved** until the admin
   decides.
3. **Confirm.** The admin records the required confirmation — *“This document contains no personal
   data of pupils, parents or staff, or I accept that it will be sent to the selected provider”* —
   and the system stores **who** confirmed, **when**, and the **exact wording**. Only then can it be
   approved and read.
4. **Warn.** Every add/edit form for a document carries the warning and says which parts are sent.

The confirmation is the decision point, not the scan: the school is accepting that the document text
will leave the server, and the record of who accepted it is kept.

### What a real provider call contains

When a provider is configured and the console or answer desk is used, the request contains exactly:

1. the **system prompt** — fixed instructions, the school name and the admin's house rules;
2. the **masked user question**, inside an explicit data fence (`<user_question>…</user_question>`);
3. from K3, the retrieved **source passages**, each inside its own `<source id="S1" title="…">` fence
   under a `<sources>` block, with a label the answer must cite.

An **embedding** request is different: it carries only the passage text (or, for a question, the
masked question) and the embedding model name — no system prompt, no conversation history, no title.

It never contains: the API key (sent only as the authentication header), the user's password, other
users' records, the database, or anything from another conversation.

### Masked request — a worked example

Question typed into the console:

> My name is Amina Wanjiku. My son David is in Form 2. Call me on 0722 123 456 or
> amina@example.com — his admission number is ADM/0234/2023. Why is the fee balance wrong?

Exactly what the console shows would be sent (and sends, for real providers):

```
<user_question>
My name is [NAME]. My son [NAME] is in Form 2. Call me on [PHONE] or
[EMAIL] — his admission number is [ID]. Why is the fee balance wrong?
</user_question>
```

The page also reports: names 2, phones 1, emails 1, IDs 1.

**Honest limits.** Masking is a deterministic heuristic, not a named-entity model: it catches the
patterns above (titles, "my name is", "call me", "I am", "jina langu ni", "ninaitwa", "my son/daughter/
child" and "mtoto wangu", Kenyan phone formats, labelled IDs, admission-path patterns). A name in
those positions must still start with a capital letter; lowercase and all-caps names, names with no
context, unusual spellings, mixed-language sentences and Sheng nicknames can get through. It makes a
leak less likely; it does not guarantee one cannot happen. The draft consent wording says exactly that,
and the page tells the person not to share personal details. Nothing extra is sent anywhere to detect
names.

Masking happens **before** the question is put in the prompt, so the unmasked text is never sent to a
provider. It is also applied to the excerpt stored on a safeguarding alert (capped at 300 characters).

## Answers, citations and the figure check

- **Unknown citation markers are stripped.** If the model invents `[S9]` when only five sources were
  given, the marker is removed and counted.
- **The figure check.** Every number, amount, date part and time part in the answer must appear in the
  text of the passages the answer cited. Commas inside numbers are ignored; numbered list markers are
  not treated as figures. Any unmatched figure, a missing valid citation, or an empty answer marks the
  answer **unverified**.
- **Unverified answers are never shown.** `knowledge/answers.py::deliver()` is the only function a
  reader-facing screen may use; it returns the fixed refusal (school contact first, English and
  Kiswahili, **DRAFT**) instead of the answer. The future chat module must call `deliver()`, not read
  the raw answer. The answer desk shows the admin the raw answer, the warning and the exact figures
  that failed, plus a panel showing exactly what the reader would receive.
- **What the check can and cannot catch.** It proves that each cited figure exists in the cited text.
  It **cannot** prove that a claim without a figure is supported, that the sentence around a figure is
  a fair summary, or that a true figure was used for the wrong point. It is a guard against invented
  money and dates, not proof of correctness. Correctness is measured by K4's question set.
- **Safeguarding runs before everything** in the desk: screen → limits → retrieval → provider →
  citations. A hit shows the rule, the safe reply and who would be alerted, and sends no alert and no
  email from a test tool.

## Knowledge documents

Knowledge files are the school's own wording and are treated as private records, not website assets.

- **Where they live.** `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`) is outside
  `MEDIA_ROOT` and is never served by the web server. `KnowledgeStorage.url()` raises, so no template
  can accidentally print a working link.
- **Names.** Uploads are stored as `knowledge/<random uuid><suffix>`. The original filename is not
  used. A download is named from the document title, not from anything the uploader typed as a path.
- **What may be uploaded.** PDF, DOCX, TXT and MD only; 20 MB; the extension must match the file's
  first bytes (a PDF must start `%PDF-`, a DOCX must be a ZIP containing `word/document.xml`, text must
  be valid UTF-8 with no NUL bytes). A renamed executable or a mislabelled archive is refused.
- **What that check is not.** It is not antivirus and does not scan macros. Files are never executed,
  never rendered inline, and never converted in this build.
- **Who can read them.** One route: the admin download view. It is admin-only, returns an attachment
  with `Content-Type` fixed from the stored suffix and `X-Content-Type-Options: nosniff`, and writes an
  audit entry naming the document. There is no public URL, no media route and no signed URL.
- **Audience before ranking.** `knowledge/scope.py::scoped_documents()` builds the SQL filter from the
  asker's identity: public documents need an empty role list and no class limit; a signed-in role sees
  public documents plus its own role's documents within its class; drafts, archived rows, documents on
  unapproved shelves and documents outside their effective dates are never candidates. Roles are a
  canonical pipe-wrapped string (`|parent|teacher|`) so the same `LIKE` filter runs on SQLite and
  PostgreSQL. Retrieval ranks only the surviving rows; it cannot widen them. The answer desk's role
  preview calls the same function through `audience_for_preview()`.
- **The ceiling is checked three times.** In `KnowledgeDocument.clean()`, in the forms (so the error
  lands on the right field), and again in `knowledge/services.py` before writing.
- **Fingerprints, not copies.** SHA-256 of the pasted text/URL or of the uploaded bytes is kept to flag
  duplicates. It is a fingerprint, not the content, and no fingerprint is sent to a provider.
- **What leaves the server at ingestion.** The text of approved documents, in passage-sized batches,
  to the configured embedding provider — and nothing else (see “Exactly what ingestion sends” above).
  Extraction itself is local; no file is uploaded anywhere. The admin screens make no provider call
  until a document is approved, its personal-data scan is resolved, and the worker reads it; embedding
  does not need the assistant's global switch.
- **The personal-data record.** Findings store the kind, heading, passage number and a masked snippet;
  the raw value is never stored. The confirmation stores who confirmed, when, and the exact wording.
- **Nothing about askers in the cache.** Answer cache keys are hashes over the role, sorted class ids,
  visible shelves, embedding model and a knowledge version; values hold the answer text, citation
  ids/titles and scores — not the question, not a user, not an IP. Cached citations are re-checked
  against live scope and effective dates on every read.
- **What is kept after fetching a web page.** The page text is stored on the document (`body_text`) so
  an admin can see exactly what was read. Fetching happens in the worker, only for `http(s)` addresses,
  and only when the document is approved.
- **Why a web page cannot reach the school's network.** Every hop (including redirects) is resolved
  and refused unless **all** its addresses are public; loopback, private, link-local and reserved ranges
  are rejected. This is an address check before the request, not a guarantee against DNS changing
  between the two — the request is still made by the HTTP library, and the system never deliberately
  follows an internal address.
- **What the fetched page can cost.** Responses are capped at 10 MB and 15 seconds, and only HTML,
  XHTML or plain text is accepted. A page is read as data, never executed or rendered.
- **Extraction safety.** pypdf and python-docx read data only; macros are never run, archives are never
  unpacked to disk, and a damaged or unsupported file becomes one plain sentence on the document page.
- **Backups.** The database and the knowledge folder must be backed up together. A restored database
  without the folder leaves rows that correctly report “no file” until the document is uploaded again.

## Consent and retention — DRAFT, awaiting approval

The wording lives in `backend/apps/assistant/consent.py` and is marked **DRAFT**. The code gate is
strict: `visible_consent()` returns nothing unless the admin has set the approval flag **and** an
approver name **and** a date. Tests assert that nothing is returned before approval.

No user-facing screen exists yet, so even an approved line cannot be shown today. When a conversation
screen ships, it will read through that gate — not from a hard-coded string.

**Draft consent line (visitor, English):**

> This assistant answers from the school's approved information. Please don't share personal details
> (yours or your child's) in the chat. We automatically mask likely names, phone numbers, emails and ID
> numbers, but masking cannot be guaranteed perfect. A member of staff can take over if needed.

**Draft (signed-in parent/student, English)** — to be finalised with the conversation module:

> This assistant can look up your own records (attendance, results, fees, homework) to answer you. It
> cannot see anyone else's. Conversations that need a person are passed to school staff.

The Kiswahili drafts are in the same file. All are proposals for the school to edit and approve.

**Retention recommendations (Kenya Data Protection Act, 2019) — DRAFT, awaiting approval:**

- Chat transcripts: **90 days**, then deleted; only aggregate counts kept.
- Safeguarding alerts: **kept until closed**, then a retention period of **12 months after closure**
  is proposed. After that the masked excerpt is erased and the **minimal record stays** (category,
  profile, raised / acknowledged / closed times, outcome, who closed it). An open alert is never
  erased. Setting the period to `0` keeps excerpts indefinitely. The period is a settings field marked
  DRAFT, and the purge is a command (`assistant_purge_alert_excerpts`, dry-run by default), not an
  automatic timer in this build.
- A deletion request from a parent or the school can reduce **one closed alert** early with
  `--alert-id`: the excerpt is erased and the erasure is audited. Alert rows and their outcomes are
  not deleted automatically; deleting a minimal record is a policy decision for the safeguarding lead
  and is not built.
- Usage rows (tokens, cost, outcome, latency): **12 months**, containing no message text.
- Knowledge gaps (masked question, role, class signature, language hint, scores): resolved gaps are
  proposed for erasure **90 days after resolution** (`GAP_RETENTION_DAYS = 90`), **DRAFT**. The admin
  can delete any gap at any time; no scheduled job deletes anything until the period is approved.
- Access logs and audit entries: follow the repository's existing audit retention.

Nothing is being retained today beyond what is listed in [data-model.md](data-model.md). The
retention field and the consent wording ship together and stay DRAFT until the school approves both.

## Safeguarding

Detection is **mandatory and cannot be switched off**. There is no setting that disables it and no
screen that edits the mandatory pack: the rules are code (`guardrails/rules.py`), versioned with the
application, and admin-added rules can only add to them. On a hit the system does not call the provider
with the message as ordinary business — it raises an alert, notifies staff, and shows a fixed safe
reply whose first line is the school's own safeguarding contact, then 999/112, then Childline Kenya
116.

If no contact is set, the reply still gives the emergency numbers and the school office, the alert is
still raised, and a banner keeps warning the admin. Switches that would put the assistant in front of a
child cannot be turned on without a named contact.

## Database privileges — proposal, not applied

The application currently connects to PostgreSQL as the database **superuser**. That is common in a
single-school deployment but wider than necessary: a bug or a compromised account could drop tables,
read every database on the cluster, or load server files.

Proposed least-privilege role (to be applied by the operator, on the host, during a maintenance window):

```sql
CREATE ROLE school_app LOGIN PASSWORD '<generated>';
GRANT CONNECT ON DATABASE <school_db> TO school_app;
GRANT USAGE ON SCHEMA public TO school_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO school_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO school_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO school_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO school_app;
```

Host changes: replace the superuser in `DATABASE_URL` with `school_app`, keep the superuser credentials
out of the application environment, and reserve them for `pg_dump`/restore and migrations run by an
operator. Migrations need `CREATE` on the schema while they run; either grant it temporarily or run
migrations as the owner role. The back-up script keeps using the owner/superuser, so backups still work.

This is deliberately **not** executed by this module. It is listed in [runbook.md](runbook.md) and on
the pilot-readiness list.

## Threat model

| Threat | Mitigation today |
|---|---|
| Database copied or leaked | Provider keys are ciphertext; the encryption key is elsewhere. Safeguarding excerpts are masked and capped. |
| Backup of the database published | Same as above; no plaintext key in any table, proved by test. |
| The server is started with no encryption key | No weak default: saving or reading a key fails closed, and system check `assistant.E001` stops `check` / `runserver` / `migrate` with the reason. |
| Key accidentally logged or shown | No logging in `crypto.py`; error text redacted; forms never re-render a saved key; templates show `masked_key` only; tests assert the raw key is absent from responses, audit entries and usage rows. |
| A key committed to git | Key-scan test over assistant code, production settings, docs and `.env.example`. |
| A non-admin opens the screens | `AdminRequiredMixin` / `RoleRequiredMixin` on every view; teacher/parent 403, visitor login redirect, tested. Alerts are visible to admin and director only. |
| Prompt injection (user text or a document pretending to be instructions) | Text and sources are fenced; fence markers inside the text are escaped; the system prompt says data is never instructions; house rules cannot override the fixed rules; the console shows the exact outbound text. |
| PII leaking to a provider | Question masking of names, phones, emails and ID-like numbers before sending; document scan + required admin confirmation before any passage is embedded; limitations documented; consent draft says so. |
| An invented fee amount or date reaching a reader | Unknown citations stripped; every figure must appear in the cited passages; anything unverified is replaced by the fixed refusal through `deliver()`. |
| A cached answer outliving its audience or an effective end date | Cache key includes role, sorted class ids and visible shelves; every cached citation is re-validated against live scope and dates at read time; a cached refusal carries no passages. |
| A document with personal data being embedded | Scan before embedding; approval and ingestion blocked until the admin confirms, recording who, when and the exact wording. |
| Test tools spending without limit | Answer desk and console have their own daily cap (default 50) and monthly ceiling (default KSh 500); the kill switch stops them too. |
| A test in the answer desk sending a real safeguarding alert | Test tools show the preview only; tests prove no alert row, no notification and no email. |
| Spending without control | Kill switch, monthly budget and optional per-provider cap checked before every call, with a cautious reservation. New switches default OFF. |
| Safeguarding disabled by an admin or a database edit | The mandatory pack is code, not rows; no setting reads it; tests prove it always loads. |
| Alert lost because the worker stopped | Escalation is also checked on every admin/director page load, throttled to once a minute; email failures are recorded and retried with a limit; in-app safety notifications bypass quiet hours. |
| A public visitor cycling cookies | Signed cookie plus IP backstop plus site-wide ceiling; a tampered cookie is replaced, not trusted. |
| A visitor guesses a knowledge file address | No URL exists: storage `url()` raises, the folder is outside `MEDIA_ROOT`, and the only read route is the admin-only download. |
| A malicious or mislabelled file is uploaded | Extension whitelist, 20 MB cap and byte-signature checks; the extension must match the contents; files are never executed or served inline. |
| A document leaks to a narrower audience | Scope filter in SQL before any ranking; ceiling enforced in model, form and service; a hand-edited row is covered by test. Passages carry no audience of their own, so a passage cannot drift away from its document. |
| A web-page document is used to probe the school's own network | Every hop's host must resolve only to public addresses; credentials in the address are refused; redirects are re-checked; only HTTP text is accepted. |
| A huge or hostile file exhausts the worker | 20 MB upload cap, 10 MB fetch cap, 60-passage limit, 15-second fetch timeout; reading runs in the worker, never in a request. |
| A failed re-index leaves old wording answering | The whole passage set is replaced in one transaction after a successful embedding; a failed read leaves the document failed, not half-updated. |
| A document is unapproved but its passages still answer | Unapproving, drafting or archiving purges the document's passages immediately; readers skip documents that are not approved. |
| A provider kind with no adapter is configured | The registry refuses it with a clear message and records the failed attempt as a usage error. |

## Known gaps in this module

- Masking is heuristic; it cannot guarantee that no name or identifier reaches a provider.
- Knowledge upload validation is type, size and byte-signature only. There is no antivirus or macro
  scan; files are simply never executed or rendered.
- Extraction is data-only and refuses private addresses, but it is not antivirus and does not scan
  macros; scanned PDFs are not read (no OCR). A page's DNS answer can change between the address check
  and the request; the system never deliberately follows an internal address.
- The personal-data scan for documents is a heuristic (patterns plus a trigger-word rule for names). It
  can miss unusual spellings, Sheng nicknames and names with no pupil/parent/guardian trigger, and it
  can flag an innocent number. The admin confirmation, not the scan, is the decision point, and the
  snippet shown is masked.
- Document text itself is sent to the embedding provider **unmasked** once the admin has confirmed the
  document. The knowledge base must contain general school information, never a pupil's record.
- The figure check proves only that cited figures exist in the cited text; it cannot prove a claim is
  supported. An answer can pass with a true figure used for the wrong point.
- Retrieval and answers exist and are exercised by the admin answer desk; no public or signed-in chat
  screen calls them yet (Module 3/4).
- The refusal wording and the 90-day gap retention are **DRAFT** for the school's approval.
- No second-layer model screening for indirect safeguarding phrasing. It is designed (with cost and
  privacy implications) and not built.
- The consent wording is unapproved, and the 12-month alert retention is a DRAFT proposal.
- `compose.yaml` does not yet forward `ASSISTANT_ENCRYPTION_KEY` into the app container. Set it in the
  host environment **and** add the pass-through line before a real provider key is saved in production.
- Deleting a safeguarding alert's minimal record is not built; only excerpt erasure is.
- A public visitor is identified by a signed cookie plus IP. If the cookie is cleared and the network
  is shared, the site-wide ceiling and budget are the remaining protection.
- Streaming is not built; the worker changes it needs are a proposal, because the current queue and
  two sync web workers cannot hold streams safely.
- The app connects to PostgreSQL as a superuser until the least-privilege plan above is applied.
- The environment key is held in the process environment, so anyone who can read the server's
  environment can read provider keys. That is inherent to server-held secrets; the runbook covers
  access limits.
