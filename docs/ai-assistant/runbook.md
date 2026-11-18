# Runbook

## First deploy

1. Install requirements: `pip install -r backend/requirements/prod.txt`. This includes `cryptography`.
2. Generate the encryption key on the server: `cd backend && python manage.py assistant_make_key`.
3. Add it to the environment: `ASSISTANT_ENCRYPTION_KEY=<printed value>`. Store it with the other
   server secrets — not in the database, a repository, or a ticket.
4. Back up the database (see below).
5. Apply migrations: `python manage.py migrate assistant`. Run `showmigrations assistant` first; the
   current head is `0005` (K3: kill switch, console limits, document personal-data fields, knowledge
   gaps). On this workstation `0005` was already applied by a concurrent tool before the module's own
   apply gate; it is additive only (see the changelog). `0004` (K2: passages and ingest runs) was
   applied on 2026-11-18 with the two tables empty afterwards.
6. Restart web and worker processes. Both need the environment variable.
7. Sign in as admin → sidebar **AI Assistant** → confirm every switch is off.
8. Keep `python manage.py qcluster` running. Knowledge reading is a background job; with no worker an
   approved document stays **Waiting to be read** and nothing is embedded. The worker does not need
   the assistant's global switch to be on.

`python manage.py check` must be clean before restarting.

## If the app refuses to start: `assistant.E001`

When `ASSISTANT_ENCRYPTION_KEY` is missing while a provider key is saved or any assistant switch is
on, `python manage.py check` (and so the deploy gate) reports `assistant.E001` and the app refuses to
start. That is deliberate: without the key, saved ciphertext cannot be read and new keys cannot be
encrypted. Generate one with `assistant_make_key` and set the variable; do not invent a weak value and
do not disable the check.

## Docker Compose — key pass-through not yet wired

`compose.yaml` does not currently forward `ASSISTANT_ENCRYPTION_KEY` into the web and worker
containers. A Compose deployment therefore starts without the key and hits `assistant.E001` as soon as
the assistant is enabled. Add the variable to both services' `environment:` (or `env_file:`) on the
host. This is recorded here as a deployment gap; the module did not edit `compose.yaml`.

## Back up

- Database: `scripts/backup_db.sh` (existing script; `pg_dump` or SQLite `.backup`, 30-day keep).
- Encryption key: separately, in the server's secret store. A database backup without the key cannot
  open saved provider keys; the key without the database is useless.
- **Knowledge files:** back up `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`) with the
  database, on the same schedule. The rows point at random file names in that folder; a database
  restored without it will show documents with no file. The folder is private: never copy it into a
  web-served directory or attach it to a ticket.
- **Passages and embeddings** live in the database (`assistant_knowledgechunk`), so the database backup
  already carries them. Text fetched from a web page is stored in the document row for the same reason.
  Re-reading a restored document is safe and replaces its passages.

## Restore

1. Stop web and worker.
2. Restore the database with `scripts/restore_db.sh` (destructive; it drops the target — the script
   documents the stop/restore/migrate/`/readyz/` sequence).
3. Restore `KNOWLEDGE_MEDIA_ROOT` from the same backup set, with the same names. Without it, uploaded
   documents still exist as rows but report “no file”; the wording has to be uploaded again.
3. Ensure `ASSISTANT_ENCRYPTION_KEY` on the restored host is the **same** key the backup's rows were
   encrypted with. If the key changed since the backup, provider keys will fail to decrypt.
4. `python manage.py migrate` then restart.
5. Check `/readyz/`, then open **AI Assistant → Providers** and press **Test** on the fake provider.

If the encryption key is lost: the database is still fine; every saved provider API key must be pasted
again (Edit → new key). There is no recovery of the old ciphertext. This is the intended trade-off.

## Rotate the encryption key

See [security-and-privacy.md](security-and-privacy.md#rotation). Short version:

1. New key first in a comma-separated value, restart.
2. Re-encrypt:
   ```
   python manage.py assistant_reencrypt_keys            # dry run, lists each provider
   python manage.py assistant_reencrypt_keys --apply
   ```
   (Or re-save each provider's key through the edit form.)
3. Remove the old key, restart again.

Verify with **Providers** (the last four characters are unchanged) and **Test**. The command refuses to
run with no key configured and never prints a key.

## Provider down or failing

- Every attempt writes a usage row with `outcome=error` and a short, redacted detail.
- The gateway tries the remaining active providers in fallback order. Timeouts and network,
  rate-limit and server errors are retried twice (1 s, then 3 s); auth and bad-request errors are not
  retried.
- After 3 failures in 60 s a provider's circuit opens for 5 minutes, and the gateway skips straight to
  the next provider. A successful call clears the circuit.
- If every provider fails, the caller gets a fixed plain message with the school office contact — never
  a stack trace.
- If an outage is long, switch the assistant off on the settings screen. The switch is checked before
  any provider call.

## Budget hit

The gateway refuses new live calls once the month's recorded spend plus a cautious reservation would
cross `monthly_budget_ksh`, or a provider's own cap. The caller sees a plain "reached its monthly
budget" message; the refusal does not call any provider. The admin raises the budget or the USD→KSh
rate on the settings screen. A budget of 0 blocks all live calls by design.

The admin answer desk and test console are separate: exempt from the live switches and the site-wide
budget, but stopped by the **kill switch**, by `console_daily_limit` (default 50 requests/day across
both) and by `console_monthly_budget_ksh` (default KSh 500). Their usage is still recorded
(`profile=console`), and those rows are what the console ceiling counts. Setting either console limit
to `0` stops the desks.

## Kill switch

**Settings → Stop everything.** It stops live conversations, the answer desk, the test console and
background reading at once. It is checked before every other switch and limit and always wins. Use it
when a provider is misbehaving or the bill needs to stop immediately; unlike the live switches, it does
not leave the desks usable.

## Public limits

The public assistant counts successful questions per signed-cookie visitor (default 10/day), per IP
(default 30/day) and site-wide (default 300/day), all admin-editable, with a Nairobi-time day boundary.
Counter cache entries expire after 26 hours; the site ceiling is counted from `UsageEvent` rows, so it
survives a restart. A signed cookie that fails verification is replaced with a new key rather than
trusted.

## Knowledge files and ingestion

- Files are stored under `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`), outside
  `MEDIA_ROOT`. Do not add a web-server route for it and do not move it under `media/`.
- Reading is a **background job** (`python manage.py qcluster`): extraction, chunking and embedding
  never run in a request. A save queues the job after the database transaction commits.
- Only approved documents on approved, effective shelves are read. Drafts, archived rows and documents
  on a draft/archived shelf are skipped. Withdrawing approval or drafting a shelf **deletes the
  document's passages immediately**.
- A successful read replaces the whole passage set in one transaction, so a failed re-index leaves the
  previous passages out of service rather than mixing old and new wording. The document page shows the
  state, passage count, model, time and the plain error.
- **Passages** are about 800 characters with about 120 characters of overlap; a document with more than
  60 passages is refused with a "split this document" message. Scanned PDFs are refused — there is no
  OCR.
- **Web pages:** only public `http(s)` addresses. Redirects (at most 3) are re-checked, the host must
  resolve only to public addresses, and the response must be HTML or plain text under 10 MB. The
  fetched text is stored on the document so an admin can see exactly what was read.
- **Cost:** each embedding batch writes `UsageEvent(source=ingestion)` rows priced from the provider's
  embedding price. The monthly and per-provider caps apply; the global assistant switch does not have
  to be on. The **kill switch does** stop embedding.
- **Personal data:** every document is scanned when it is added and before it is embedded. A document
  with findings stays out of service until an admin confirms the wording on the document page. The
  findings store only kind, heading, passage number and a masked snippet.
- **Needs re-index** appears when the embedding model changed (or a provider using that model was
  deleted) after indexing. Those documents are ignored until re-read.

### Re-index

```
cd backend
python manage.py reindex_knowledge                 # only documents flagged Needs re-index
python manage.py reindex_knowledge 12 15           # these document ids
python manage.py reindex_knowledge --all           # every approved document
python manage.py reindex_knowledge --all --now     # read now, ignoring the queue
```

Without `--now` the command queues work and needs a running `qcluster`. It refuses drafts/archived rows
with a reason and never prints document text. The document page's **Read it again** button queues the
same job for one document and is audited.

### When reading fails

| What the document page says | Meaning | What to do |
|---|---|---|
| **Waiting to be read** | Queued; the worker has not picked it up. | Confirm `qcluster` is running. |
| **Could not be read** with "password-protected" | The PDF is encrypted. | Remove the password and upload again. |
| **Could not be read** with "no readable text" | The PDF is a scan. | Upload a text version; no OCR is built. |
| **Could not be read** with "too long … more than 60 passages" | One document is too large. | Split it into smaller documents. |
| **Could not be read** with "Personal data was detected" | The scan found a phone, email, ID-like number or likely name and nobody has confirmed it. | Review the findings on the document page; edit the document, or confirm it, then press **Read it again**. |
| **Could not be read** with a provider reason | The embedding provider failed permanently or no provider has an embedding model. | Fix the provider or set an embedding model, then press **Read it again**. |
| **Needs re-index** | The embedding model changed after indexing. | Press **Read it again** or run the command. |

## Answers, the answer desk and gaps

- Retrieval runs in the request that asks: one question embedding (cached 30 days) and one chat call,
  both through the gateway, so the kill switch, budgets and circuit breaker apply.
- Answers are cached for one hour. The cache key is a hash of the role, sorted class ids, visible
  shelves, model signature and knowledge version; every cached citation is re-checked against live
  scope and effective dates, so an expired document stops being used immediately. To force a fresh
  answer for everyone, the knowledge version changes when documents change; clearing the cache
  (`assistant:answer:*`) is safe at any time.
- An answer is shown only when it cites a valid passage and every figure in it appears in the cited
  text. Otherwise the reader would receive the fixed refusal; the answer desk shows the admin the raw
  answer and why it failed.
- Gaps record only the masked question, role, class ids, language hint, reason, top score and test
  flag. Test-tool gaps are hidden from the default list. The admin can resolve or delete any gap.
  Resolved-gap erasure after **90 days** is a DRAFT proposal; no scheduled job runs it.

## Safeguarding alerts and escalation

- A hit creates the alert row first, then in-app notifications to every active admin and director
  (`category="safety"`, bypasses quiet hours), then an email to the safeguarding contact, the handoff
  address and every active admin/director with an email.
- Email failures are recorded in **Failed jobs** and retried up to 3 times.
- Escalation runs from the dashboard middleware whenever an admin or director loads any page
  (throttled to once a minute), so a stopped worker cannot hide an overdue alert. Outside working
  hours the clock starts at the next working hour.
- Acknowledge on **AI Assistant → Alerts**. Acknowledgement does not start the retention clock.

## Safeguarding alert closure and retention

- Closing an alert (**AI Assistant → Alerts → Close**) requires a recorded outcome and stores who
  closed it and when. The school's safeguarding process decides the outcome wording.
- Retention is **DRAFT** (proposal awaiting approval with the consent wording): 12 months after
  closure, set by `alert_retention_months` on the settings screen. `0` disables purging entirely.
- The purge erases only the masked excerpt; the minimal record (category, rule, status, who, when)
  stays. **Open alerts are never erasable**, whatever the setting.
- Purge is dry-run by default:

  ```
  python manage.py assistant_purge_alert_excerpts                    # report only
  python manage.py assistant_purge_alert_excerpts --apply            # erase due excerpts
  python manage.py assistant_purge_alert_excerpts --alert-id 42 --apply   # one deletion request
  ```

  Use `--alert-id` for a parent's or pupil's erasure request; the command refuses an alert that is
  still open or only acknowledged.

## Worker reliability

The assistant app does not swallow task exceptions. Failed assistant jobs are recorded in
`AssistantJobFailure` and shown under **Failed jobs** with the reason and attempt count. Other apps'
jobs keep their existing behaviour; changing them needs a separate decision.

There is still one shared django-q2 queue with 2 workers and no failure acknowledgement. Nothing a user
waits for should depend on it; the assistant's request path is synchronous with a hard timeout. A
dedicated assistant queue is part of the streaming proposal.

## Database privileges — proposal, not applied

The application currently connects as the PostgreSQL superuser. The proposed least-privilege role and
the exact host changes are in
[security-and-privacy.md](security-and-privacy.md#database-privileges--proposal-not-applied).

Summary: create `school_app` with `CONNECT`, `USAGE` on `public`, and CRUD on tables/sequences; point
`DATABASE_URL` at it; keep the owner/superuser credentials for migrations and backups only. Migrations
need `CREATE` on the schema while they run. Do this in a maintenance window, with a backup, and verify
`/readyz/` plus a read and a write afterwards.

## Streaming and worker changes — proposal, not applied

Do not enable chat streaming on the current tier. The exact changes (Gunicorn workers/timeout/threads,
a dedicated assistant queue, nginx buffering, Cloudflare check) are listed in
[architecture.md](architecture.md#10-streaming-and-perceived-speed-proposal--not-built) and need
approval first.

## Rollback

`python manage.py migrate assistant 0004` removes the K3 fields and the knowledge gap table while
keeping the K2 passages and ingest runs; no document, passage or answer text is deleted by it.
`python manage.py migrate assistant 0003` removes the two K2 tables (passages and ingest runs) and the
`ingestion` choices, while keeping the knowledge catalogue. `python manage.py migrate assistant 0002`
removes the four knowledge catalogue tables (documents, versions, question-and-answer pairs and
collections) while keeping the Module 1 tables. Neither deletes files in `KNOWLEDGE_MEDIA_ROOT`;
remove those separately if that is what you intend. `python manage.py migrate assistant 0001` removes
the Step 2 tables and fields while keeping the original three tables and their data.
`python manage.py migrate assistant zero` removes all thirteen tables, including settings, providers,
usage history, gap questions and alert records (saved keys are encrypted, but the rows go too). All are
destructive; take a backup first and only do this deliberately.
