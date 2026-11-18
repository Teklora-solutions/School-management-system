# Admin guide

Everything here is done from the admin account, in the browser. No developer tools needed. The
screens are designed to work on a phone: one column, large tap targets, no sideways scrolling.

## Where to find it

Sidebar → **AI Assistant**. The tabs across the top are:

| Tab | Address | Who can open it |
|---|---|---|
| Settings | `/assistant/settings/` | admin |
| Providers | `/assistant/providers/` | admin |
| Knowledge | `/assistant/knowledge/documents/` | admin |
| Safeguarding rules | `/assistant/guardrails/` | admin |
| Test console | `/assistant/console/` | admin |
| Alerts | `/assistant/alerts/` | admin and director |
| Failed jobs | `/assistant/jobs/` | admin |

A thin banner appears at the top of **every** staff page when the safeguarding contact is missing or
when an alert is still open, with a link straight to the alerts page. You do not have to remember to
look.

The assistant is **off** when you first see it. Nothing is sent anywhere until you switch it on, and
there is still no screen where a parent, student, teacher or visitor can talk to it. This version
sets up and proves the machinery behind that screen, lets you catalogue the approved documents it will
answer from, and reads those documents into passages in the background. Nothing searches or answers
from the passages yet.

## 1. Set the rules

**AI Assistant → Settings**. The page is in five groups.

### Switches — all start OFF

| Switch | What it controls |
|---|---|
| **Assistant switched on (global)** | The master switch. Off stops every new conversation immediately. |
| **Public assistant on the website** | Visitors with no account. Needs the master switch too. |
| **Signed-in assistant** | Parents, students, teachers, director and admin. Needs the master switch too. |
| **Student tutor mode** | A study-help mode for students. Needs the signed-in switch and a named safeguarding contact. |

Turning the signed-in assistant or the tutor on requires a **named safeguarding contact** with a phone
number or an email. The form refuses otherwise, and the banner keeps reminding you until it is set.
The tutor additionally cannot be on while the signed-in assistant is off.

### Provider and rules

| Field | What it does |
|---|---|
| **Default provider (tried first)** | Which provider gets the first call. The rest follow in fallback order. Empty until you add one. |
| **House rules** | Standing instructions: tone, language (English/Kiswahili), and what it must never say. This is rules, not knowledge — documents come later. |

### Money

| Field | What it does |
|---|---|
| **Monthly budget, all providers (KSh)** | The site-wide ceiling. `0` means no spending at all — which blocks every non-console call. |
| **USD → KSh rate** | Converts the provider prices you enter in dollars into the KSh budget. A positive budget cannot be saved with a zero rate. |

The check runs **before** each provider call: this month's recorded spend plus a cautious reservation
for the call being attempted must fit under the ceiling, or the call is refused with a plain message.
Each provider can also have its own optional cap on the provider form.

### Limits

| Field | Default | What it does |
|---|---|---|
| **Messages per signed-in person per day** | 20 | Per user per day. `0` means no messages. |
| **Public: questions per visitor per day** | 10 | One visitor is a **signed cookie** on their device. `0` disables the public assistant. |
| **Public: questions per IP per day** | 30 | A backstop against one device cycling cookies. Must sit above the visitor limit. |
| **Public: questions per day, whole site** | 300 | The ceiling for the entire public assistant. |

Why not per-IP alone: on Kenyan mobile networks thousands of families share one carrier address, so an
IP-only limit either locks out a whole neighbourhood after a few questions or has to be set so high it
protects nothing. The cookie gives each visitor a fair share; the IP backstop stops casual evasion; the
site ceiling and the budget are the real safety net. All four numbers are yours to change.

The day boundary is **Nairobi time**. Successful messages count; blocked ones do not.

### Safeguarding contact and escalation

| Field | What it does |
|---|---|
| **Safeguarding contact name / phone / email** | The person a child or parent is told to reach **first** in the safe reply, before the emergency numbers. |
| **Escalate an unacknowledged alert after (minutes)** | Default 15. Outside working hours the clock starts at the next working hour. |
| **Working hours start / end** | Set both to the same time to escalate around the clock. |
| **DRAFT: erase a closed alert's excerpt after (months)** | Default 12. The proposed retention period. Counted from the day an alert is closed, not from the day it was raised. `0` keeps excerpts indefinitely. Awaiting your approval — see §6. |

When the timer passes, the director and admin are notified again in the app and by email. The check
also runs whenever an admin or director opens any page, so a stopped background worker cannot hide an
unacknowledged alert.

### Handoff

**Handoff email** — where conversations that need a person are sent. Safeguarding alert emails also go
to this address, the safeguarding contact and every active admin and director.

### Consent wording — DRAFT, gated

The page shows the current **draft** consent text in English and Kiswahili and says clearly that it is
not approved. Nothing user-facing can display it until you tick **Consent wording approved** and enter
**who approved it** and **the date**. The form refuses a flag without a name and date, and a name/date
without the flag. The retention numbers in [security-and-privacy.md](security-and-privacy.md) are
proposals until you approve them.

Click **Save settings**. Every save is written to the audit log by field name; the text of the house
rules and the consent wording is not copied into the audit trail.

## 2. Add a provider

**AI Assistant → Providers → Add provider**.

| Field | What it does |
|---|---|
| **Name** | A label you recognise. Must be unique. |
| **Kind** | Fake (development/tests), OpenAI, DeepSeek, Google Gemini, or OpenAI-compatible server. |
| **Chat model** | The main model name. Required for every kind except the fake one. |
| **Fast/cheap model** | Optional cheaper model for simple public questions (used when chat screens arrive). |
| **Embedding model** | Used by knowledge search (Module 2). Changing it later means re-indexing every document. |
| **Server address** | Required for an OpenAI-compatible server; optional elsewhere. The form shows the usual base URL for each provider. |
| **API key** | Encrypted before saving; afterwards shown only as its last four characters. |
| **Timeout (seconds)** | How long one call may take. 1–120. |
| **Available for use** | Off means it cannot be chosen as default or tested, and the gateway skips it. |
| **Fallback order** | Lower numbers are tried earlier. The default provider is always tried first. |
| **Input / output / embedding price** | USD per **1 million tokens**, exactly as the provider publishes. Used for the budget. Leave 0 if you do not know; the call still records tokens. |
| **Provider monthly cap (KSh)** | Optional. Blank means only the site-wide budget applies. |
| **Where this provider processes data** | Your note, e.g. "United States". Shown as a reminder; it does not change behaviour. |

Model names change often. Copy the exact name from the provider's own console or API documentation
when you add the provider; this guide does not pin model names, so it cannot go stale.

Click **Save provider**. You land on the provider list.

### If the screen says the encryption key is missing

The server has no `ASSISTANT_ENCRYPTION_KEY` configured. A keyless provider (the fake one) still saves,
but a real API key is refused rather than stored in plain text. Ask whoever looks after the server to
run:

```
cd backend
python manage.py assistant_make_key
```

and put the printed value in the environment file as `ASSISTANT_ENCRYPTION_KEY`. Store it with the
other server secrets — not in a document that travels by email, and never in the database.

## 3. Get a key and test a provider

Do this once per real provider, in this order. The fake provider needs no key and no account, so you
can rehearse the whole flow before you create a real one.

### Checklist before a real key

1. **Create the API key** in the provider's own console (OpenAI, DeepSeek or Google AI Studio). Give
   it a name you will recognise, such as “School assistant — pilot”.
2. **Set a spending limit** in that console as well as the key. Our monthly budget is a second,
   independent brake; it cannot stop a provider from charging you, so the provider-side cap is the
   one that bounds the bill. Set it low for the pilot.
3. **Copy the key once.** Most providers show it only at creation. Do not paste it into chat,
   email, a document or a ticket.
4. **Paste it** into **Providers → Add provider → API key** and press **Save provider**. The page
   then shows only its last four characters.
5. **Press Test** on the provider row. This sends one canned sentence (“Reply with the word hello”)
   through the real gateway to that provider only. A success proves the key authenticates and the
   model answers. It writes one usage row and one audit entry; no user message is sent.

The test is the authentication proof: a key that is merely saved, never tested, is not known to
work. Do not switch the assistant on until a Test has succeeded.

### What each failure means

The screen shows a plain sentence and, in quotes, the provider's own words (“Provider said: …”).
Nothing about the key is written to the log, the audit trail or the page.

| What you see | What failed | What to do |
|---|---|---|
| **rejected the API key** (401/403) | The key itself: wrong, revoked, from another project, or the provider does not allow this kind of call. | Create a new key in the provider console and paste it. Check the key belongs to the account with the spending limit. |
| **could not find that model** (404) | Usually the **model name**, or the endpoint for that model is not enabled for this key. | Copy the exact model id from the provider's current documentation. For Gemini, use the name without the `models/` prefix if the console shows one. |
| **wrong model name or setting** (400/405/409/422) | The request was understood but rejected: a model that does not accept this call, or a setting the model does not support. | Check the chat model; try the provider's standard chat model first. |
| **rate limit or quota** (429) | The provider's own **spending limit, quota or plan** is exhausted or throttled. | Open the provider console; raise or wait out the limit. Our budget is separate and does not cause this message. |
| **did not answer in time** | **Network/timeout**: the server waited and gave up. | Test again; if it repeats, check the server's internet route and the timeout field. |
| **could not reach the provider** | **Network**: DNS, firewall, proxy or no outbound internet from the server. | Ask whoever looks after the server; the fake provider still works, so the assistant can be prepared meanwhile. |
| **reported a server error** (5xx) | The provider's side failed. | Retry later or let the fallback provider answer. |
| **no encryption key configured** | The server has no `ASSISTANT_ENCRYPTION_KEY`, so a saved key cannot be read. | See §2. This is a server setting, not a provider problem. |

Gemini and DeepSeek use different headers and paths from OpenAI, and the adapters send the right ones
for each kind. If a header or endpoint is wrong, the message above is what you will see; the key is
never echoed back.

Each test writes one usage row and one audit entry. No user message is ever sent by this button.
Testing works even while the assistant is switched off, so you can prove a key before go-live.

## 4. The test console

**AI Assistant → Test console** runs a question through the same pipeline a real conversation will use,
without creating alert rows or touching any real person's data:

- the mandatory safeguarding rules run first; a hit shows the fixed safe reply and **does not call the
  provider**;
- the question is masked (names, phones, emails, ID-like numbers) and the page shows **exactly the text
  that would be sent**, plus how many of each kind were masked;
- the reply, model, latency and token counts are shown.

The console is exempt from budgets and daily limits so you can verify a provider before switching the
assistant on. Its usage is still recorded.

Try a few real questions here before you trust the assistant with parents.

## 5. Safeguarding rules

**AI Assistant → Safeguarding rules** has two parts.

- **Mandatory rules** — the built-in list, grouped by category (self-harm, abuse, neglect, threat,
  medical, secrecy/coercion), with the trigger phrases visible. They are marked **Cannot be removed**.
  There is no edit or delete button because they are not database rows; a database restore cannot
  remove them either.
- **Your rules** — admin-added phrases, one per line, matched on word boundaries. You can add and
  switch them off; adding is always allowed, removing the mandatory floor never is.

The built-in list is a **floor**, not a classifier. It catches the direct phrasing listed; indirect
phrasing may get past it. It is deliberately biased toward false alarms: a staff member losing a minute
is cheaper than a missed child.

## 6. Alerts

**AI Assistant → Alerts** (admin and director). Each row shows the masked excerpt, category, profile
(user or IP), time, and whether it has been escalated. Press **Acknowledge** once a person has taken it
on.

When the concern has been dealt with, open the alert and press **Close**, then record **what was
done** (who was contacted, what happens next). The outcome is required — the alert cannot be closed
with a blank box. Closing records your name and the time, keeps the row, and starts the retention
clock. Acknowledging never reopens a closed alert.

Notification order on a hit: alert row first, then in-app notification to every active admin and
director (safety notifications ignore quiet hours), then email. If email fails it is retried up to
three times and the failure is listed under **Failed jobs**.

### Keeping and erasing — DRAFT

An alert is **never deleted automatically**. The proposed rule, awaiting your approval, is:

- a closed alert keeps its masked excerpt for **12 months after it is closed**;
- after that, the excerpt is erased and the **minimal record stays**: category, profile, raised /
  acknowledged / closed times, outcome and who closed it;
- an open alert is never erasable;
- setting the retention field to `0` turns erasure off entirely and keeps excerpts indefinitely.

The purge runs as the `assistant_purge_alert_excerpts` command (dry-run by default), so it can be
scheduled or run by hand. See [runbook.md](runbook.md). These numbers and the consent wording are
**DRAFT** until you approve them.

### If a parent or the school asks for deletion

This build does not delete alert rows. What it can do, once the safeguarding lead agrees the record
may be reduced:

1. **Close the alert first**, with the outcome, if it is still open. An open alert cannot be reduced.
2. Run the purge command for that one alert with `--alert-id`. It erases the masked excerpt
   immediately and records that it was erased and why. The minimal record stays.
3. The free-text **outcome** is staff notes and is not erased by the command. If it must be reduced,
   that is a deliberate edit by the safeguarding lead, not an automatic step.

Whether the minimal record itself may be deleted is the school's policy decision, taken with the
safeguarding lead; the assistant does not make it on its own. A parent's request is matched to alerts
by the signed-in user where the alert has one; public-visitor alerts have only an IP address and may
not be attributable.

## 7. Failed jobs

**AI Assistant → Failed jobs** lists assistant background failures with the reason and attempt count.
Press **Resolve** when you have dealt with one. Assistant jobs do not swallow their errors; other
parts of the system keep their existing behaviour.

## 8. Changing or replacing a key

Provider list → **Edit**. Leave **API key** blank to keep the saved key. Paste a new key to replace it;
the old one is gone once you save. The list then shows only the new last four characters.

For a **server key rotation**, see [security-and-privacy.md](security-and-privacy.md#rotation) and the
`assistant_reencrypt_keys` command in [runbook.md](runbook.md).

## 9. Knowledge: the documents the assistant may answer from

**AI Assistant → Knowledge** opens the **Knowledge documents** list. Two screens work together:
**Collections** (the shelves) and **Documents** (the wording itself). Everything here is admin-only.

A plain-language rule for the whole section: a document is a **source of facts**, never an
instruction. The assistant will not follow text inside a document; it only quotes and cites it.

### 9.1 Shelves (collections)

**Knowledge → Collections → Add a collection.** Start with the usual shelves: Admissions, Fees,
Calendar, Policies, Parent Guide, Teacher Guide, Student Help, Subject Notes.

| Field | What it does |
|---|---|
| **Name** | The shelf label, e.g. “Fees”. Must be unique. |
| **Slug** | The short address form. Leave blank to make one from the name. |
| **Description** | A sentence on what belongs here. |
| **Audience** | **Public** (anyone on the website, including visitors) or **Signed-in** (only people with an account). This is the *ceiling* for the shelf. |
| **Who may read it** | Role boxes (Parent, Teacher, Student, Finance, Director, Admin). Empty means every signed-in role. A Public shelf must leave this empty. |
| **Classes** | Optional class limit. Empty means every class. |
| **Status** | Draft, Approved or Archived. Only an **Approved** shelf can supply answers. |
| **Effective from / to** | Optional dates. Leave both blank for “always”. |

The screen shows each shelf with its audience, status, effective window and “approved / total”
document count.

**The ceiling rule.** A document may be **narrower** than its shelf, never broader. If the shelf is
Signed-in for parents and teachers, a document cannot be Public, cannot add the student role, and
cannot add a class the shelf does not allow. The form refuses with a sentence under the field that
caused it, for example: *“The document is public but the collection is signed-in.”* You never have to
work it out from a combined error at the top of the page.

### 9.2 Add a document — four ways

**Knowledge → Add a document** shows four cards:

| Card | For | What happens now |
|---|---|---|
| **Upload a file** | PDF, Word (.docx), plain text or Markdown | Stored privately under a random name. 20 MB limit. The file's first bytes must match its extension (a renamed program is refused). |
| **Add a web page** | Wording that lives on the school website or another public page | Only the address is saved at first. The page is fetched when the approved document is read, and its text is kept on the document so you can see what was read. The link must be public `http`/`https`, cannot contain a username or password, and cannot point at a private network address. |
| **Paste text** | Wording the school has already checked | Saved as the document's text. At least 20 characters. |
| **Questions and answers** | Short, exact answers a parent or pupil may ask for | At least one complete question-and-answer pair; blank rows are ignored. |

Every document starts on the form with status **Draft**. You can change the status before saving if
the wording is already approved, but the safe habit is: save as Draft, read it on the document page,
then approve it.

Each form also asks for:

| Field | What it does |
|---|---|
| **Collection** | The shelf. Required. |
| **Title** | Shown in citations later, so make it recognisable (“Fees — Form 2, Term 1”). |
| **Language** | English or Kiswahili, for the document itself. |
| **Audience / roles / classes** | Must be narrower than or equal to the shelf. |
| **Status and effective dates** | As on the shelf. |

### 9.3 The document page

Clicking a document title shows:

- **Details** — shelf, who may read it, language, effective window, owner, added date, the web address
  or the stored file's random name, and a short content or file fingerprint.
- **Possible duplicate** — an amber warning linking to an earlier document with the same file bytes or
  the same pasted text. The system does not merge them; keep one so the same wording is not cited
  twice.
- **Needs re-index** — a red warning that appears when the embedding model changed after indexing.
  The document is ignored until it is read again.
- **Saved text and questions and answers** — exactly what was pasted or entered.
- **Version history** — every save that changes the title writes a version row with a note, who saved
  it and when. Version 1 is created when the document is added.
- **Reading and answers** — reading state, passages stored, embedding model, last read time, the last
  error in plain words, a **Read it again** button and the five most recent reads. **Test this
  document** is visible but disabled until retrieval arrives in K3.
- **Change status** — swap between Draft, Approved and Archived. The change is audited.
- **Download file** — only for uploaded documents. The download is an attachment, marked
  `X-Content-Type-Options: nosniff`, and written to the audit log. There is no public link to a
  knowledge file anywhere; the private folder is outside the website's media folder.

### 9.4 The documents list

Filters: title search, shelf, status and type. The subtitle counts approved documents out of all
documents, and repeats the rule: **only approved documents on approved shelves, inside their effective
dates, are ever used.** That rule is enforced in the database query that selects candidates, before any
ranking exists.

### 9.5 Reading and indexing

When you approve a document — and its shelf is approved and inside its dates — the system queues a
background read. It extracts the text, splits it into passages of about 800 characters, asks the
provider's **embedding model** to turn each passage into numbers, and stores the passages. Nothing is
sent to the provider until then, and the assistant's global switch does **not** have to be on.

| Where you see it | What it means |
|---|---|
| **Waiting to be read** | Queued. The background worker has not picked it up. If it stays here, the worker is not running. |
| **Reading now** | The worker is working on it. |
| **Ready** | Passages are stored. The count, model and time are shown. |
| **Could not be read** | A plain reason is shown under the state (encrypted PDF, a scan with no text, too long, provider failure). Fix the cause and press **Read it again**. |
| **Not read** | The document or its shelf is a draft/archived, so no passages are kept. |
| **Needs re-index** | The embedding model changed after this document was read. It is ignored until read again. |

Things that queue a read automatically:

- approving a document, or saving an edit to an approved document;
- approving a shelf that has approved documents.

Things that remove passages immediately:

- setting a document or its shelf to Draft or Archived;
- an effective date window that means the shelf is not in force.

**Read it again** (on the document page) queues one document and is written to the audit log. It is
refused while the document is already being read, or when the document or shelf is not approved.

A document with **more than 60 passages** is refused with a message asking you to split it. A scanned
PDF is refused because there is no OCR — upload a text version instead. A re-read replaces the whole
passage set, so old wording never sits beside new wording.

The background worker must be running (`python manage.py qcluster`). Without it, documents wait
indefinitely; nothing on the site breaks, but nothing is searchable later either.

### 9.6 What is honestly not working yet

- Passages exist, but nothing searches them: no question can be answered from them yet (K3).
- Nothing is shown to a parent, pupil, teacher or visitor.
- “Test this document” is deliberately disabled until retrieval exists.
- Language support is single-language per document: there is no automatic translation.
- Upload only **demo** documents in this phase — never a real pupil's record, a real invoice or a real
  safeguarding note. Document text is sent to the embedding provider unmasked.

Reading and indexing are K2, now built; retrieval, citations and refusal are K3; the question set and
scores are K4, all inside Module 2.

## 10. What the admin cannot do yet

- There is no screen where a parent, student, teacher or visitor talks to the assistant.
- Knowledge is uploaded, organised and read into passages, but nothing retrieves or answers from them
  yet (Module 2 K3–K4).
- The public/signed-in switches are recorded decisions; the public widget, handoff rules and human
  inbox arrive in Module 3, the signed-in screens and record tools in Module 4.
- Handoff records and routing arrive with those screens. Safeguarding email already sends.
- Streaming replies are not built; the worker changes needed first are a proposal awaiting approval.
- The consent wording cannot be shown to anyone, even after approval, until the conversation screen
  exists.

Each of those arrives in its own module, with this guide updated in the same change.
