# Runbook

## First deploy

1. Install requirements: `pip install -r backend/requirements/prod.txt`. This includes `cryptography`.
2. Generate the encryption key on the server: `cd backend && python manage.py assistant_make_key`.
3. Add it to the environment: `ASSISTANT_ENCRYPTION_KEY=<printed value>`. Store it with the other
   server secrets — not in the database, a repository, or a ticket.
4. Back up the database (see below).
5. Apply migrations: `python manage.py migrate assistant`. Run `showmigrations assistant` first; the
   current head is `0003` (knowledge tables).
6. Restart web and worker processes. Both need the environment variable.
7. Sign in as admin → sidebar **AI Assistant** → confirm every switch is off.

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

The gateway refuses new non-console calls once the month's recorded spend plus a cautious reservation
would cross `monthly_budget_ksh`, or a provider's own cap. The caller sees a plain "reached its monthly
budget" message; the refusal does not call any provider. The admin raises the budget or the USD→KSh
rate on the settings screen. A budget of 0 blocks all non-console calls by design.

The admin test console is exempt so a provider can be verified before go-live; its usage is still
recorded.

## Public limits

The public assistant counts successful questions per signed-cookie visitor (default 10/day), per IP
(default 30/day) and site-wide (default 300/day), all admin-editable, with a Nairobi-time day boundary.
Counter cache entries expire after 26 hours; the site ceiling is counted from `UsageEvent` rows, so it
survives a restart. A signed cookie that fails verification is replaced with a new key rather than
trusted.

## Knowledge files and ingestion

- Files are stored under `KNOWLEDGE_MEDIA_ROOT` (default `backend/private_knowledge`), outside
  `MEDIA_ROOT`. Do not add a web-server route for it and do not move it under `media/`.
- In K1 nothing reads the files. `ingestion_state` stays `pending`; the admin download is the only
  access path and every download is audited.
- When K2 arrives, extraction, chunking and embedding run in the worker — never in a request. Until
  then, a large upload costs nothing and blocks nothing.
- A document flagged **Needs re-index** means the embedding model changed after it was indexed. It is
  ignored until re-indexed (K2 builds the re-index action).

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

`python manage.py migrate assistant 0002` removes the four knowledge tables (documents, versions,
question-and-answer pairs and collections) while keeping the Module 1 tables. It does **not** delete
files in `KNOWLEDGE_MEDIA_ROOT`; remove those separately if that is what you intend.
`python manage.py migrate assistant 0001` removes the Step 2 tables and fields while keeping the
original three tables and their data. `python manage.py migrate assistant zero` removes all ten tables,
including settings, providers, usage history and alert records (saved keys are encrypted, but the rows
go too). All are destructive; take a backup first and only do this deliberately.
