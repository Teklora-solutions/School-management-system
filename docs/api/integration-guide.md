# Integration guide

This guide is written for developers at external organisations — payment companies, government
portals, mobile app teams, LMS providers — who want to connect their system to this school
management platform. No access to the school's server or codebase is required. Everything here is
done over HTTP.

---

## Contents

1. [Before you start](#1-before-you-start)
2. [Getting an API key](#2-getting-an-api-key)
3. [Making your first request](#3-making-your-first-request)
4. [Reading school data](#4-reading-school-data)
5. [Receiving events (subscribing to webhooks)](#5-receiving-events-subscribing-to-webhooks)
6. [Sending events to the school (inbound webhooks)](#6-sending-events-to-the-school-inbound-webhooks)
7. [Authentication reference](#7-authentication-reference)
8. [Errors](#8-errors)
9. [Rate limits](#9-rate-limits)
10. [Going live checklist](#10-going-live-checklist)
11. [SDK examples](#11-sdk-examples)
12. [Support and versioning](#12-support-and-versioning)

---

## 1. Before you start

### What you need

- An API key from the school's administrator (see [§2](#2-getting-an-api-key)).
- The school's base URL, e.g. `https://greenfield.school`.
- An HTTPS endpoint on your side if you want to receive webhook events.

### Base URL

Every API path starts with:

```
https://<school-domain>/api/v1/
```

The school's administrator will confirm the exact domain. All examples in this guide use
`https://school.example.com` as a placeholder — replace it with the real domain.

### Interactive reference

A live, clickable API reference is available at:

```
https://school.example.com/api/v1/schema/swagger-ui/
```

You can try every endpoint directly from the browser once you have a key. No local setup needed.

---

## 2. Getting an API key

API keys are issued by the school administrator. Contact them and ask for:

1. A key for your integration.
2. The specific **scopes** you need (the administrator controls these — request only what you need).
3. The school's base URL.

### Available scopes

| Scope | What it allows |
|---|---|
| `students:read` | Read student records, enrolment status, class placement |
| `academics:read` | Read academic years, terms, classes, subjects |
| `staff:read` | Read teacher profiles |
| `results:read` | Read published exam results and report card data |
| `finance:read` | Read fee invoices and outstanding balances |
| `attendance:read` | Read attendance registers |
| `attendance:write` | Submit attendance entries |
| `webhooks:manage` | Register webhook endpoints and manage API clients |

Request only the scopes your integration needs. A `finance:read` key cannot touch student records,
and a `students:read` key cannot see fees — this is enforced server-side regardless of what your
code does.

### Key format

Keys look like this:

```
sk_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8
```

The `sk_` prefix identifies it as an API key. Keep it secret — treat it like a password. Do not
commit it to version control or include it in client-side JavaScript.

---

## 3. Making your first request

Pass the key in the `Authorization` header of every request:

```
Authorization: Token sk_<your-key>
```

### Try it now

```bash
curl https://school.example.com/api/v1/academic-years/ \
  -H "Authorization: Token sk_<your-key>"
```

A successful response looks like:

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "name": "2025/2026",
      "start_date": "2025-09-01",
      "end_date": "2026-07-31",
      "is_current": true
    },
    {
      "name": "2024/2025",
      "start_date": "2024-09-01",
      "end_date": "2025-07-31",
      "is_current": false
    }
  ]
}
```

If you get a 401, the key is wrong or was not passed correctly.  
If you get a 403, the key is valid but does not have the required scope for that endpoint.

---

## 4. Reading school data

### Pagination

All list endpoints are paginated. The default page size is 25. Use the `page` query parameter
to move through results:

```bash
# Page 1 (default)
GET /api/v1/students/

# Page 2
GET /api/v1/students/?page=2
```

Every list response has this envelope:

```json
{
  "count": 120,
  "next": "https://school.example.com/api/v1/students/?page=2",
  "previous": null,
  "results": [ ... ]
}
```

`count` is the total across all pages. `next` and `previous` are full URLs — you can follow them
directly.

### Filtering and search

Most list endpoints support filters as query parameters:

```bash
# Active students only
GET /api/v1/students/?status=active

# Students in a specific class
GET /api/v1/students/?current_class__name=Grade+5

# Search by name or admission number
GET /api/v1/students/?search=kofi

# Current term only
GET /api/v1/terms/?is_current=true
```

### Students

**List all students** — requires `students:read`

```bash
GET /api/v1/students/
```

```json
{
  "count": 240,
  "results": [
    {
      "admission_number": "GA/26/001",
      "full_name": "Kofi Mensah",
      "email": "kofi@student.example.com",
      "gender": "M",
      "status": "active",
      "current_class": "Grade 5 (2025/2026)",
      "current_stream": "Blue",
      "enrollment_date": "2022-09-05"
    }
  ]
}
```

**Get one student** — requires `students:read`

```bash
GET /api/v1/students/GA%2F26%2F001/
```

> Note: URL-encode the `/` in admission numbers: `GA/26/001` → `GA%2F26%2F001`

**Get a student's published results** — requires `students:read`

```bash
GET /api/v1/students/GA%2F26%2F001/results/
```

Returns only results whose status is `published`. Draft, submitted and confirmed sheets are
never returned here — the school controls when results become visible.

```json
[
  {
    "school_class": "Grade 5 (2025/2026)",
    "subject": "MATH – Mathematics",
    "term": "2025/2026 – Term 1",
    "stream": null,
    "status": "published",
    "published_at": "2026-04-15T10:30:00+03:00",
    "scores": [
      {
        "student": "GA/26/001",
        "student_name": "Kofi Mensah",
        "raw_score": "78.00",
        "total": "78.00",
        "grade": "B+",
        "remark": "Good",
        "points": "3.50"
      }
    ]
  }
]
```

**Get a student's fee invoices** — requires `students:read` and `finance:read`

```bash
GET /api/v1/students/GA%2F26%2F001/fees/
```

```json
[
  {
    "invoice_number": "INV-2026-0042",
    "student": "GA/26/001",
    "term": "2025/2026 – Term 1",
    "total_amount": "2400.00",
    "amount_paid": "1200.00",
    "amount_outstanding": "1200.00",
    "status": "posted",
    "issued_date": "2026-01-10",
    "due_date": "2026-01-31"
  }
]
```

Monetary amounts are always returned as strings to avoid floating-point precision loss. Parse
them with a decimal library, not a float.

### Academic structure

```bash
# All academic years
GET /api/v1/academic-years/

# A specific year
GET /api/v1/academic-years/2025%2F2026/

# All terms
GET /api/v1/terms/

# Current term only
GET /api/v1/terms/?is_current=true

# All subjects
GET /api/v1/subjects/

# A specific subject by code
GET /api/v1/subjects/MATH/

# All classes (optionally filtered by year)
GET /api/v1/classes/?academic_year__name=2025%2F2026
```

### Teachers / staff

```bash
# All teachers
GET /api/v1/staff/

# One teacher by staff ID
GET /api/v1/staff/TCH001/
```

### Attendance

**Read attendance entries** — requires `attendance:read`

```bash
GET /api/v1/attendance/?date=2026-11-26&status=absent
```

**Submit an attendance register** — requires `attendance:write`

```bash
POST /api/v1/attendance/
Content-Type: application/json

{
  "school_class": "Grade 5",
  "academic_year": "2025/2026",
  "date": "2026-11-26",
  "entries": [
    {"admission_number": "GA/26/001", "status": "present"},
    {"admission_number": "GA/26/002", "status": "absent", "reason": "ill"},
    {"admission_number": "GA/26/003", "status": "late"}
  ]
}
```

Valid status values: `present`, `absent`, `late`, `excused`.

Submitting the same register twice (same class + date) updates existing entries rather than
duplicating them — the operation is idempotent.

---

## 5. Receiving events (subscribing to webhooks)

If you want your system to be notified automatically when things happen at the school — a new
enrolment, a fee payment, a result published — register a webhook endpoint. The school's platform
will POST a signed JSON payload to your URL every time a matching event fires.

### Requirements on your side

- An HTTPS endpoint that accepts POST requests.
- The ability to verify an HMAC-SHA256 signature (see [Verifying signatures](#verifying-signatures)).
- A response of HTTP 200 within 15 seconds. Return 200 immediately and process asynchronously
  if your handler is slow.

### Step 1 — Register your endpoint

```bash
POST /api/v1/webhooks/endpoints/
Authorization: Token sk_<your-key-with-webhooks:manage-scope>
Content-Type: application/json

{
  "name": "My LMS",
  "url": "https://lms.example.com/hooks/school-events",
  "events": ["student.enrolled", "result.published"]
}
```

**Response:**

```json
{
  "id": "a3f1c2d4-...",
  "name": "My LMS",
  "url": "https://lms.example.com/hooks/school-events",
  "secret": "4a7b2c9d1e3f...",
  "events": ["student.enrolled", "result.published"],
  "is_active": true,
  "consecutive_failures": 0,
  "disabled_at": null,
  "created_at": "2026-11-26T10:00:00+03:00"
}
```

**Save the `secret` immediately.** It is returned only once and is not retrievable again. You will
use it to verify every incoming request.

To subscribe to all event types, pass `"events": ["*"]`.

### Step 2 — Verifying signatures

Every POST the school sends to your endpoint includes these headers:

```
X-Webhook-Signature: sha256=<hex-digest>
X-Webhook-Event:     student.enrolled
X-Delivery-ID:       a1b2c3d4-e5f6-...
User-Agent:          SchoolMS-Webhook/1.0
```

Verify the signature **before processing the payload**. If verification fails, return 401 and
discard the request.

The digest is `HMAC-SHA256(raw_request_body, your_secret)`.

**Python**

```python
import hashlib
import hmac
from django.http import HttpResponse

def my_webhook_handler(request):
    signature_header = request.META.get("HTTP_X_WEBHOOK_SIGNATURE", "")
    secret = "4a7b2c9d1e3f..."  # from registration

    body = request.body
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, received):
        return HttpResponse(status=401)

    # Safe to process now
    import json
    event = json.loads(body)
    print(event["event"])  # "student.enrolled"

    return HttpResponse(status=200)
```

**Node.js**

```javascript
const crypto = require("crypto");

function verifySignature(body, secret, header) {
  const expected = crypto
    .createHmac("sha256", secret)
    .update(body)
    .digest("hex");
  const received = header.replace("sha256=", "");
  return crypto.timingSafeEqual(
    Buffer.from(expected),
    Buffer.from(received)
  );
}

app.post("/hooks/school-events", (req, res) => {
  const raw = req.rawBody; // must be the raw buffer, not parsed JSON
  const sig = req.headers["x-webhook-signature"];

  if (!verifySignature(raw, "4a7b2c9d1e3f...", sig)) {
    return res.status(401).send("Invalid signature");
  }

  const event = JSON.parse(raw);
  console.log(event.event); // "student.enrolled"
  res.status(200).send("OK");
});
```

**PHP**

```php
$secret  = "4a7b2c9d1e3f...";
$body    = file_get_contents("php://input");
$header  = $_SERVER["HTTP_X_WEBHOOK_SIGNATURE"] ?? "";

$expected = "sha256=" . hash_hmac("sha256", $body, $secret);

if (!hash_equals($expected, $header)) {
    http_response_code(401);
    exit;
}

$event = json_decode($body, true);
echo $event["event"]; // "student.enrolled"
http_response_code(200);
```

> Always use a constant-time comparison (`hmac.compare_digest`, `crypto.timingSafeEqual`,
> `hash_equals`). A regular string comparison leaks timing information.

### Step 3 — Handle the payload

Every event payload follows the same envelope:

```json
{
  "event": "payment.received",
  "timestamp": "2026-11-26T10:30:00+03:00",
  "data": {
    "student": "GA/26/001",
    "amount": "1200.00",
    "term": "2025/2026 – Term 1"
  }
}
```

The `data` object is event-specific. See the event catalogue below.

### Event catalogue

| Event | Fires when | Key `data` fields |
|---|---|---|
| `student.enrolled` | A new student is registered | `admission_number`, `full_name`, `current_class`, `enrollment_date` |
| `student.updated` | A student record is changed | `admission_number`, `full_name` |
| `student.status_changed` | A student's status changes (e.g. withdrawn) | `admission_number`, `old_status`, `new_status` |
| `student.promoted` | A student moves to the next class | `admission_number`, `from_class`, `to_class` |
| `academic_year.created` | A new academic year is added | `name`, `start_date`, `end_date` |
| `term.started` | A term is marked as current | `name`, `academic_year` |
| `result.published` | A result sheet is published | `school_class`, `subject`, `term` |
| `invoice.created` | A fee invoice is generated | `invoice_number`, `student`, `term`, `total_amount` |
| `payment.received` | A fee payment is recorded | `student`, `amount`, `term` |
| `payment.reversed` | A payment reversal is posted | `student`, `amount`, `term` |
| `attendance.recorded` | An attendance register is completed | `school_class`, `date`, `present_count`, `absent_count` |

### Delivery behaviour and retries

If your endpoint is unavailable or returns a non-2xx status:

| Attempt | Retry after |
|---|---|
| 1st failure | 5 minutes |
| 2nd failure | 15 minutes |
| 3rd failure | 45 minutes |
| 4th failure | 135 minutes |
| 5th failure | Marked abandoned — no further retries |

After 10 consecutive failures across all deliveries, the endpoint is automatically disabled.
The school administrator can re-enable it.

Use the `X-Delivery-ID` header to deduplicate retries on your side — the same delivery ID is
sent on every retry attempt for the same event.

### Managing your endpoints

```bash
# List your registered endpoints
GET /api/v1/webhooks/endpoints/

# View delivery history (useful for debugging)
GET /api/v1/webhooks/deliveries/?status=failed

# Update an endpoint (e.g. change URL or events)
PUT /api/v1/webhooks/endpoints/<id>/

# Delete an endpoint
DELETE /api/v1/webhooks/endpoints/<id>/
```

---

## 6. Sending events to the school (inbound webhooks)

If your system needs to push data to the school — for example, a payment gateway confirming a
transaction — you can POST to the school's inbound webhook endpoint.

**This requires the school administrator to register your system first.** Ask them to add your
integration to their `INBOUND_WEBHOOK_SOURCES` configuration and give you:

1. The inbound URL slug assigned to your integration, e.g. `paystack`
2. A shared secret for signing your requests

Your inbound URL will be:

```
POST https://school.example.com/api/v1/inbound/<your-slug>/
```

### Signing your requests

Sign every request you send with `HMAC-SHA256(raw_body, shared_secret)` and include the header:

```
X-Webhook-Signature: sha256=<hex-digest>
Content-Type: application/json
```

The school's platform verifies this signature before processing. Requests without a valid
signature receive a 401.

### Example — payment confirmation

```bash
POST https://school.example.com/api/v1/inbound/paystack/
Content-Type: application/json
X-Webhook-Signature: sha256=4a7b2c...

{
  "event": "charge.success",
  "data": {
    "reference": "PAY-2026-00123",
    "amount": 120000,
    "currency": "GHS",
    "customer": {
      "email": "parent@example.com"
    }
  }
}
```

**Return 200 immediately** after the school responds — do not wait for processing. The school
queues your payload and handles it in the background.

---

## 7. Authentication reference

### API key

```
Authorization: Token sk_<your-key>
```

- Passed in every request header.
- Keys do not expire automatically — the school administrator revokes them manually.
- If a key is revoked you receive 401. Contact the school to get a new one.

### JWT (alternative — for user-facing apps)

If you are building an app where real school users log in with their own credentials, use JWT
instead of an API key.

```bash
# Step 1 — Obtain tokens
POST /api/v1/auth/token/
Content-Type: application/json

{
  "username": "admin@school.example.com",
  "password": "..."
}

# Response
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}

# Step 2 — Use the access token
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Step 3 — Refresh when it expires (after 1 hour)
POST /api/v1/auth/token/refresh/
Content-Type: application/json

{ "refresh": "eyJ..." }
```

Refresh tokens expire after 7 days and are rotated on use — the old refresh token is invalidated
and a new one is issued in the response. Store the new one.

---

## 8. Errors

Every error response uses the same envelope regardless of the type of error:

```json
{
  "error": {
    "code": "authentication_failed",
    "message": "Invalid or revoked API key.",
    "detail": null
  }
}
```

For validation errors, `detail` contains a field-keyed breakdown:

```json
{
  "error": {
    "code": "invalid",
    "message": "Validation failed.",
    "detail": {
      "school_class": ["Class not found for the given academic year."],
      "date": ["This field is required."]
    }
  }
}
```

### Error codes

| HTTP status | Code | What it means | What to do |
|---|---|---|---|
| 400 | `invalid` | Request body failed validation | Fix the fields listed in `detail` |
| 401 | `authentication_failed` | Missing key, wrong key, or revoked key | Check the `Authorization` header; contact the school if the key was recently working |
| 403 | `permission_denied` | Key is valid but lacks the required scope | Ask the school administrator to add the scope to your key |
| 404 | `not_found` | The resource does not exist | Check the identifier (e.g. admission number) |
| 405 | `method_not_allowed` | Wrong HTTP method | Check the endpoint reference |
| 415 | `unsupported_media_type` | Missing or wrong `Content-Type` | Add `Content-Type: application/json` to POST requests |
| 429 | `throttled` | Too many requests | Back off and retry after the `Retry-After` header value (seconds) |
| 500 | `error` | Server-side error | Retry after a short delay; report to the school if it persists |

---

## 9. Rate limits

| Endpoint group | Limit |
|---|---|
| All REST API endpoints | 1,000 requests per hour per API key |
| Inbound webhook (`/inbound/`) | 200 requests per minute |
| Auth token endpoints | 20 requests per minute |

When you exceed a limit, the response is HTTP 429 with the header:

```
Retry-After: 47
```

The value is the number of seconds to wait before retrying. Implement exponential back-off in
your client for resilience.

---

## 10. Going live checklist

Work through this before switching your integration to production traffic.

### On your side

- [ ] API key is stored in an environment variable or secrets manager — not in code or `.env`
      files committed to version control.
- [ ] All requests use HTTPS — not plain HTTP.
- [ ] Webhook handler verifies the `X-Webhook-Signature` header before processing any payload.
- [ ] Webhook handler returns HTTP 200 immediately and processes the event asynchronously.
- [ ] Webhook handler uses the `X-Delivery-ID` header to deduplicate retried deliveries.
- [ ] Monetary amounts are parsed with a decimal library — not a float.
- [ ] Admission numbers in URLs are percent-encoded (`/` → `%2F`).
- [ ] Pagination is handled — your code follows `next` links or iterates pages, not just page 1.
- [ ] Rate limit errors (429) are handled with back-off and retry.
- [ ] Errors (4xx / 5xx) are logged and alerted on — not silently ignored.

### Ask the school administrator to confirm

- [ ] Your API key has only the scopes you actually use.
- [ ] Your server's IP address is in the key's allowlist (if the school is using that feature).
- [ ] The webhook endpoint URL is reachable from the school's server.
- [ ] For inbound webhooks: your slug is registered in the school's configuration.
- [ ] Contact details are exchanged for incident response.

---

## 11. SDK examples

No official SDK is published yet. The API follows standard REST conventions and works with any
HTTP library. The examples below cover the most common operations.

### Python

```python
import httpx
from decimal import Decimal

BASE_URL = "https://school.example.com/api/v1"
API_KEY  = "sk_<your-key>"

headers = {"Authorization": f"Token {API_KEY}"}

# --- Fetch all active students ---
def get_active_students():
    students = []
    url = f"{BASE_URL}/students/"
    params = {"status": "active"}

    while url:
        resp = httpx.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        students.extend(data["results"])
        url = data["next"]
        params = {}  # next URL already includes params

    return students

# --- Get one student's fee balance ---
def get_fee_balance(admission_number: str) -> Decimal:
    encoded = admission_number.replace("/", "%2F")
    resp = httpx.get(f"{BASE_URL}/students/{encoded}/fees/", headers=headers)
    resp.raise_for_status()
    invoices = resp.json()
    total_outstanding = sum(
        Decimal(inv["amount_outstanding"]) for inv in invoices
    )
    return total_outstanding

# --- Submit an attendance register ---
def submit_attendance(school_class, academic_year, date, entries):
    resp = httpx.post(
        f"{BASE_URL}/attendance/",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "school_class": school_class,
            "academic_year": academic_year,
            "date": date,
            "entries": entries,
        },
    )
    resp.raise_for_status()
    return resp.json()
```

### JavaScript (Node.js / fetch)

```javascript
const BASE_URL = "https://school.example.com/api/v1";
const API_KEY  = process.env.SCHOOL_API_KEY;

const headers = {
  "Authorization": `Token ${API_KEY}`,
  "Content-Type": "application/json",
};

// Fetch all pages of a paginated endpoint
async function fetchAll(path) {
  const results = [];
  let url = `${BASE_URL}${path}`;

  while (url) {
    const resp = await fetch(url, { headers });
    if (!resp.ok) throw new Error(`API error ${resp.status}`);
    const data = await resp.json();
    results.push(...data.results);
    url = data.next;
  }
  return results;
}

// Get one student's published results
async function getStudentResults(admissionNumber) {
  const encoded = encodeURIComponent(admissionNumber);
  const resp = await fetch(
    `${BASE_URL}/students/${encoded}/results/`,
    { headers }
  );
  if (!resp.ok) throw new Error(`API error ${resp.status}`);
  return resp.json();
}
```

### PHP

```php
<?php
define("BASE_URL", "https://school.example.com/api/v1");
define("API_KEY",  getenv("SCHOOL_API_KEY"));

function api_get(string $path, array $params = []): array {
    $url = BASE_URL . $path;
    if ($params) $url .= "?" . http_build_query($params);

    $ctx = stream_context_create(["http" => [
        "header" => "Authorization: Token " . API_KEY . "\r\n",
        "method" => "GET",
    ]]);
    $body = file_get_contents($url, false, $ctx);
    if ($body === false) throw new RuntimeException("API request failed");
    return json_decode($body, true);
}

// Fetch all students (follows pagination)
function get_all_students(): array {
    $students = [];
    $path     = "/students/";
    $params   = ["status" => "active"];

    do {
        $data = api_get($path, $params);
        array_push($students, ...$data["results"]);
        $next   = $data["next"] ?? null;
        $path   = $next ? parse_url($next, PHP_URL_PATH) : null;
        $params = [];
    } while ($path);

    return $students;
}
```

---

## 12. Support and versioning

### API versioning

The current API version is **v1**, reflected in every URL (`/api/v1/`). Breaking changes — removed
fields, changed field types, removed endpoints — will be introduced only in a new version (`/api/v2/`),
and v1 will remain available for a migration period. Additive changes (new fields, new endpoints)
may be made to v1 without notice.

### What counts as a breaking change

- Removing a field from a response
- Renaming a field
- Changing a field's type
- Removing an endpoint
- Changing required authentication for an endpoint

### What does not count as a breaking change

- Adding a new field to a response
- Adding a new endpoint
- Adding a new event type to the webhook catalogue
- Adding a new scope
- Changing error messages (use the `code` field for programmatic handling, not the message text)

### Getting help

| Issue | Contact |
|---|---|
| API key request or scope change | School administrator |
| Unexpected 500 errors | School administrator — provide the timestamp and `X-Request-ID` response header |
| Webhook deliveries not arriving | Check `/api/v1/webhooks/deliveries/?status=failed` for error detail |
| Inbound webhook returning 401 | Check your signing secret matches what the school registered |
| Feature requests | School administrator — they escalate to the platform developer |

When reporting a problem, always include:

1. The full request URL
2. The HTTP method
3. The response status code and body
4. The `X-Request-ID` header from the response (present on every response — links to server logs)
5. The timestamp (UTC or with timezone)
