# Admin guide

Everything here is done from the admin account, in the browser. No developer tools needed.

## Where to find it

Sidebar → **AI Assistant** (under School Settings). The settings page is at `/assistant/settings/`;
providers are at `/assistant/providers/`.

The assistant is **off** when you first see it. Nothing is sent anywhere until the school decides
otherwise, and there is no conversation screen yet — this version only sets up who will answer and
under what rules.

## 1. Set the rules

On **AI Assistant → Settings**:

| Field | What it does |
|---|---|
| **Assistant switched on** | The school's decision to use the assistant. Off is the safe default. In this version it is a recorded decision; conversation screens arrive later. |
| **Default provider** | Which provider new conversations will use. Empty until you add one. |
| **House rules** | Standing instructions: tone, language (English/Kiswahili), and what it must never say. This is rules, not knowledge — documents come later. |
| **Monthly budget (KSh)** | The ceiling for spending. `0` means no spending at all. The ceiling is recorded now; enforcement arrives with the budget module. |
| **Messages per person per day** | A per-person daily cap. `0` means no messages allowed. Recorded now, enforced later. |
| **Handoff email** | Where conversations that need a person go. Used when handoff is built. Safeguarding escalation will use it too, and cannot be switched off. |

Click **Save settings**. Every save is written to the audit log by field name — the text of the rules
is not copied into the audit trail.

## 2. Add a provider

**AI Assistant → Providers → Add provider**.

In this version there is one kind: **Fake provider (development and tests)**. It answers inside the
server and contacts nobody. Real providers (OpenAI-compatible, Anthropic, and so on) arrive with the
knowledge module; the list will grow then, not before.

| Field | What it does |
|---|---|
| **Name** | A label you recognise, e.g. "Fake provider". Must be unique. |
| **Kind** | Which adapter answers. Only the fake one exists today. |
| **Model name** | Optional for the fake provider; will matter for real providers. |
| **Server address** | Used by real providers, which are not available yet. |
| **API key** | Optional for the fake provider. When you do paste a key it is encrypted before saving and never shown again — only the last four characters stay visible. |
| **Timeout** | How long a call may take, in seconds. |
| **Available for use** | Off means the provider cannot be chosen as the default or tested. |

Click **Save provider**. You land on the provider list.

### If the screen says the encryption key is missing

The server has no `ASSISTANT_ENCRYPTION_KEY` configured. Settings and a keyless fake provider still
work. Ask whoever looks after the server to run:

```
cd backend
python manage.py assistant_make_key
```

and put the printed value in the environment file as `ASSISTANT_ENCRYPTION_KEY`. That key is the only
thing that can open saved provider keys — store it with your other server secrets, not in a document
that travels by email.

## 3. Test the connection

Provider list → **Test** on a row.

One canned prompt goes through the gateway. The fake provider answers immediately; the page says what
it replied. Each test writes one row to the usage log (tokens in, tokens out, outcome) and one audit
entry. No user message is ever sent by the test.

## 4. Changing or replacing a key

Provider list → **Edit**. Leave **API key** blank to keep the saved key. Paste a new key to replace it;
the old one is gone once you save. The list then shows only the new last four characters.

## 5. What the admin cannot do yet

- There is no screen where a parent, student, teacher or visitor talks to the assistant.
- Knowledge cannot be uploaded.
- Budgets and daily limits are recorded but not enforced.
- Handoff does not send email yet.
- Safeguarding detection is not built — which is one reason there is no conversation screen.

Each of those arrives in its own module, with this guide updated in the same change.
