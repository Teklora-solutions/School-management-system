# Security and privacy

## API keys

### How a key is held

- The key is encrypted with **Fernet** (AES-128-CBC + HMAC, from the `cryptography` package) before
  it touches the database. What is stored is an opaque token plus the last four characters.
- The encryption key itself is **not** in the database, not in git, and not in any backup of the
  database. It lives in the server environment as `ASSISTANT_ENCRYPTION_KEY`
  (`backend/config/settings/base.py`). It can be generated with
  `python manage.py assistant_make_key`.
- Screens show `•••• 1234` and nothing else. The audit trail records that a key was set or replaced
  (`key_set`, `key_updated`, `key_last4`) and never the value.
- `apps/assistant/crypto.py` contains no logging. Tests assert the raw key never appears in the
  provider list, the settings page or any audit entry.
- The Django admin site does not register these models, so keys are not browsable there.

### Rotation

The environment variable accepts a comma-separated list, newest first:

```
ASSISTANT_ENCRYPTION_KEY=NEW_KEY,OLD_KEY
```

Encryption always uses the first key; decryption tries each key in turn. To rotate:

1. Generate a new key: `python manage.py assistant_make_key`.
2. Put it **first**, keep the old one after the comma, and restart the app.
3. Open each provider and paste its API key again (last four characters confirm the right key), or
   re-save through the edit form with the key pasted. This re-encrypts with the new key.
4. Remove the old key from the environment and restart.

Step 3 is required: without re-saving, rows remain encrypted under the old key, and removing it in
step 4 makes them unreadable. The app will say so rather than silently lose them.

### What to back up

The database **and** the encryption key, separately. A database backup without the key cannot reveal
saved provider keys; a key without the database is useless. Never put the key inside a database
backup or a ticket.

## What leaves the system

**Today: nothing.** The only provider adapter is the fake one, which runs in-process. The admin
connection test sends a fixed canned sentence to that adapter. No visitor or user message is sent
anywhere because no conversation screen exists.

When a real provider arrives, this section will list exactly what is sent; the working rule is that
personal identifiers are minimised before text leaves the server, and that rule is enforced in code
before the first real provider call ships.

## Consent and retention — awaiting your approval, not yet implemented

Nothing below is shown to users today. It is a recommendation for the module where the assistant
first speaks to a person (Module 2/4). Nothing user-facing will be published before you approve it.

**Proposed consent line (visitor, English):**

> This assistant answers from the school's approved information. Please don't share personal details
> (yours or your child's) in the chat. A member of staff can take over if needed.

**Proposed consent line (signed-in parent/student, English):**

> This assistant can look up your own records (attendance, results, fees, homework) to answer you. It
> cannot see anyone else's. Conversations that need a person are passed to school staff.

**Kiswahili drafts** will be prepared for your review at the same time.

**Retention recommendation (Kenya Data Protection Act, 2019):**

- Chat transcripts: **90 days**, then deleted; only aggregate counts kept.
- Safeguarding escalations: **kept until the school's safeguarding process closes them**, with the
  reason recorded — these must not be silently deleted.
- Usage rows (tokens, cost, outcome): **12 months**, containing no message text.
- Access logs and audit entries: follow the repository's existing audit retention.

These are proposals; the retention code does not exist yet and nothing is being retained today.

## Safeguarding

Detection of a child talking about harm, abuse, self-harm or an emergency is **mandatory and cannot
be switched off by the admin** — that is a product rule, not a setting. It is not built yet (Module 2,
before any conversation screen). Until it is, the assistant has no way to speak to a child, which is
deliberate.

## Threat model (Module 1)

| Threat | Mitigation today |
|---|---|
| Database copied or leaked | Provider keys are ciphertext; the encryption key is elsewhere. Only last-four fragments are readable. |
| Backup of the database published | Same as above; no plaintext key in any table. |
| Key accidentally logged or shown | No logging in `crypto.py`; forms never re-render a saved key (`render_value=False`); templates show `masked_key` only; tests assert the raw key is absent from responses and audit entries. |
| A non-admin opens the screens | `AdminRequiredMixin` on every view; teacher/parent/visitor access is tested (403 or login redirect). |
| A provider kind with no adapter is configured | The registry refuses it with a clear message and records the failed attempt as a usage error. |
| Prompt injection (user text pretending to be instructions) | No prompt is built yet; the rule is recorded in [architecture.md](architecture.md) and applies from the first conversation module. |
| Spending without control | Budget and daily-cap fields exist; enforcement is not built, and the only adapter costs nothing. Do not add a real provider adapter before enforcement ships. |

## Known gaps in this module

- No runtime check that the encryption key is strong beyond Fernet's own validation.
- Key rotation depends on an operator following the four steps above; there is no automated
  re-encryption command yet.
- The environment key is held in the process environment, so anyone who can read the server's
  environment can read provider keys. That is inherent to server-held secrets; the runbook covers
  access limits.
