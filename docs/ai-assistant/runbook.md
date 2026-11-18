# Runbook

## First deploy (Module 1)

1. Install requirements: `pip install -r backend/requirements/prod.txt`. This now includes
   `cryptography`.
2. Generate the encryption key on the server:
   `cd backend && python manage.py assistant_make_key`
3. Add it to the environment:
   `ASSISTANT_ENCRYPTION_KEY=<printed value>`
   Store it with the other server secrets. Do not put it in the database, a repository, or a ticket.
4. Back up the database (see below).
5. Apply migrations: `python manage.py migrate assistant`
6. Restart web and worker processes. Both need the environment variable; the worker does not use it
   today but will when provider calls move off the request path.
7. Sign in as admin → sidebar **AI Assistant** → confirm the "switched off" notice appears.

`python manage.py check` must be clean before restarting.

## Back up

- Database: `scripts/backup_db.sh` (existing script; `pg_dump` or SQLite `.backup`, 30-day keep).
- Encryption key: separately, in the server's secret store. A database backup without the key cannot
  open saved provider keys; the key without the database is useless.

## Restore

1. Stop web and worker.
2. Restore the database with `scripts/restore_db.sh` (destructive; it drops the target — the script
   documents the stop/restore/migrate/`/readyz/` sequence).
3. Ensure `ASSISTANT_ENCRYPTION_KEY` on the restored host is the **same** key the backup's rows were
   encrypted with. If the key changed since the backup, provider keys will fail to decrypt.
4. `python manage.py migrate` then restart.
5. Check `/readyz/`, then open **AI Assistant → Providers** and press **Test** on the fake provider.

If the encryption key is lost: the database is still fine; every saved provider API key must be
pasted again (Edit → new key). There is no recovery of the old ciphertext. This is the intended
trade-off.

## Rotate the encryption key

See [security-and-privacy.md](security-and-privacy.md#rotation). Short version:

1. New key first in a comma-separated value, restart.
2. Re-save each provider's API key so it is encrypted with the new key.
3. Remove the old key, restart again.

Verify with **Providers** (last-four unchanged after re-save) and **Test**.

## Provider down

No real provider is wired yet. When one is:

- The gateway records a usage row with `outcome=error` and a short, safe detail (never a key).
- The user-facing message must say the assistant is unavailable and offer the handoff contact.
- If the provider is down for a long time, the admin switches the assistant off; the switch is
  honoured before any provider call.

## Budget hit

Not enforced yet. When it is, the intended behaviour is: the gateway refuses new messages once the
month's estimated spend reaches `monthly_budget_ksh`, tells the user the assistant is unavailable and
offers the handoff contact, and writes a usage row with `outcome=error`. The admin raises the budget on
the settings screen.

## Rollback

`python manage.py migrate assistant zero` removes the three tables. It deletes settings, providers and
usage history, including saved keys (which are encrypted anyway). Only do this deliberately, with a
backup taken first.
