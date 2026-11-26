# External Integration API

The school management system exposes a REST API at `/api/v1/` that lets external systems — payment
gateways, government EMIS portals, mobile apps, LMS platforms — read and write data securely without
touching the HTML interface.

**Live reference:** once the server is running, visit `/api/v1/schema/swagger-ui/` for the
interactive Swagger UI, or `/api/v1/schema/redoc/` for the ReDoc layout. Both are generated
automatically from the code and are always current.

---

## Contents

1. [Architecture overview](#1-architecture-overview)
2. [Authentication](#2-authentication)
3. [Scopes and access control](#3-scopes-and-access-control)
4. [Endpoints](#4-endpoints)
5. [Outbound webhooks (you subscribe)](#5-outbound-webhooks-you-subscribe)
6. [Inbound webhooks (you push to us)](#6-inbound-webhooks-you-push-to-us)
7. [Error format](#7-error-format)
8. [Rate limiting](#8-rate-limiting)
9. [Wiring a new integration](#9-wiring-a-new-integration)
10. [Configuration reference](#10-configuration-reference)
11. [Known limitations](#11-known-limitations)

---

## 1. Architecture overview

```
External System
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 1 — Inbound REST API          /api/v1/           │
│  External systems pull data from us (students, results, │
│  fees, attendance, academic structure).                  │
├─────────────────────────────────────────────────────────┤
│  Layer 2 — Inbound webhooks          /api/v1/inbound/   │
│  External systems push events to us (payment gateway    │
│  confirmations, EMIS enrolment pushes, etc.).           │
├─────────────────────────────────────────────────────────┤
│  Layer 3 — Outbound webhooks         (background tasks) │
│  We push events to subscribed endpoints when business   │
│  actions complete (student enrolled, payment received,  │
│  result published, etc.).                               │
└─────────────────────────────────────────────────────────┘
      │
      ▼
  Service layer  (apps/*/services.py — existing rules unchanged)
```

### Code layout

```
backend/apps/api/
├── models.py          APIClient, WebhookEndpoint, WebhookDelivery
├── authentication.py  APIKeyAuthentication — sk_… token lookup by SHA-256 hash
├── permissions.py     require_scope() factory + role→scope mapping for JWT callers
├── exceptions.py      Uniform JSON error envelope for every error response
├── serializers.py     Read/write serializers for every exposed resource
├── views.py           10 ViewSets (academics, students, staff, attendance, webhooks, clients)
├── webhooks.py        dispatch_event(), deliver_webhook() background task, retry sweep
├── inbound.py         InboundWebhookView — signature verification + async dispatch
├── urls.py            DRF router + JWT endpoints + OpenAPI schema endpoints
├── admin.py           Django admin for all three models
├── schema.py          drf-spectacular extension — describes APIKeyAuthentication in OpenAPI
└── apps.py            AppConfig — registers the schema extension on startup
```

### Design decisions

- **Separate identity for machines.** `APIClient` is not a `User`. Revoking one integration key
  touches nothing else; creating one does not create a login account.
- **Keys are stored as SHA-256 hashes.** The plain-text key is returned once at creation and
  never stored. A database breach does not leak working credentials.
- **Scopes are explicit.** Every client carries a list of scope strings (`students:read`,
  `finance:read`, etc.). A client without a scope cannot reach that resource regardless of
  how it was created.
- **Finance writes go through the service layer.** The API never issues direct ORM saves against
  finance models. The append-only ledger rules are therefore enforced for all callers.
- **Never expose internal PKs.** Natural keys are used throughout: `admission_number` for students,
  `code` for subjects and accounts, `name` for years/terms. External systems are decoupled from
  auto-increment IDs.

---

## 2. Authentication

The API supports two authentication schemes. Use API keys for server-to-server integrations;
use JWT for user-facing mobile apps.

### 2.1 API key (recommended for server-to-server)

Include the key in every request header:

```
Authorization: Token sk_<your-key>
```

Keys are created by an admin in the Django admin panel (`/django-admin/api/apiclient/add/`) or
via the API itself (`POST /api/v1/clients/`). The plain-text key is shown exactly once at creation.

**Example:**
```bash
curl https://school.example.com/api/v1/students/ \
  -H "Authorization: Token sk_abc123..."
```

### 2.2 JWT (for mobile apps / user-facing clients)

Obtain tokens with the user's login credentials:

```bash
# Obtain tokens
curl -X POST https://school.example.com/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin@school.edu", "password": "..."}'

# Response
{
  "access": "eyJ...",
  "refresh": "eyJ..."
}

# Use the access token
curl https://school.example.com/api/v1/students/ \
  -H "Authorization: Bearer eyJ..."

# Refresh when the access token expires (1 hour)
curl -X POST https://school.example.com/api/v1/auth/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "eyJ..."}'
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/auth/token/` | POST | Obtain access + refresh tokens |
| `/api/v1/auth/token/refresh/` | POST | Refresh an expired access token |
| `/api/v1/auth/token/verify/` | POST | Verify a token is still valid |

JWT access tokens expire after **1 hour**. Refresh tokens expire after **7 days** and are
rotated on use (old refresh token is blacklisted automatically).

---

## 3. Scopes and access control

Every API client holds a list of scope strings. A request is rejected (403) if the client does not
hold the required scope for the endpoint being called.

### Available scopes

| Scope | Grants access to |
|---|---|
| `students:read` | List and retrieve student records |
| `students:write` | Create and update student records (reserved — not yet exposed) |
| `academics:read` | Academic years, terms, subjects, classes |
| `staff:read` | Teacher profiles and assignments |
| `results:read` | Published result sheets and score entries |
| `finance:read` | Fee invoices, balances, outstanding amounts |
| `finance:write` | Confirm inbound payments (via webhook handler) |
| `attendance:read` | Attendance registers and entries |
| `attendance:write` | Submit attendance registers via POST |
| `webhooks:manage` | Register outbound webhook endpoints; manage API clients |

### JWT callers (real users)

When a request is authenticated with a JWT (a real `User` account), the user's `role` is mapped
to implicit scopes:

| Role | Implicit scopes |
|---|---|
| `admin` | All scopes |
| `director` | students:read, academics:read, staff:read, results:read, finance:read, attendance:read |
| `teacher` | students:read, academics:read, results:read, attendance:read + attendance:write |
| `finance` | students:read, finance:read + finance:write |
| `parent` | students:read, results:read, finance:read |
| `student` | results:read, finance:read |

---

## 4. Endpoints

All endpoints are under `/api/v1/`. Responses are JSON. Paginated lists return:

```json
{
  "count": 120,
  "next": "/api/v1/students/?page=2",
  "previous": null,
  "results": [...]
}
```

Default page size is 25. Use `?page=N` to paginate.

### Academics

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/academic-years/` | `academics:read` | List all academic years |
| GET | `/api/v1/academic-years/{name}/` | `academics:read` | One year by name |
| GET | `/api/v1/terms/` | `academics:read` | All terms. Filter: `?is_current=true` |
| GET | `/api/v1/subjects/` | `academics:read` | Active subjects. Search: `?search=math` |
| GET | `/api/v1/subjects/{code}/` | `academics:read` | One subject by code |
| GET | `/api/v1/classes/` | `academics:read` | Classes. Filter: `?academic_year__name=2025%2F2026` |

### Students

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/students/` | `students:read` | List students. Filter: `?status=active`, `?gender=M`, `?current_class__name=Grade+5`. Search: `?search=john` |
| GET | `/api/v1/students/{admission_number}/` | `students:read` | Full student record |
| GET | `/api/v1/students/{admission_number}/results/` | `students:read` | Published results only |
| GET | `/api/v1/students/{admission_number}/fees/` | `students:read` + `finance:read` | Fee invoices with balances |

### Staff

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/staff/` | `staff:read` | All teacher profiles. Search: `?search=smith` |
| GET | `/api/v1/staff/{staff_id}/` | `staff:read` | One teacher by staff ID |

### Attendance

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/attendance/` | `attendance:read` | Entries. Filter: `?status=absent`, `?date=2026-11-26` |
| POST | `/api/v1/attendance/` | `attendance:write` | Submit a register (see body below) |

**POST body for attendance:**
```json
{
  "school_class": "Grade 5",
  "academic_year": "2025/2026",
  "date": "2026-11-26",
  "entries": [
    {"admission_number": "GA/26/001", "status": "present"},
    {"admission_number": "GA/26/002", "status": "absent", "reason": "sick"}
  ]
}
```

### Webhook management

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/webhooks/endpoints/` | `webhooks:manage` | List registered endpoints |
| POST | `/api/v1/webhooks/endpoints/` | `webhooks:manage` | Register a new endpoint |
| GET | `/api/v1/webhooks/endpoints/{id}/` | `webhooks:manage` | One endpoint |
| PUT | `/api/v1/webhooks/endpoints/{id}/` | `webhooks:manage` | Update an endpoint |
| DELETE | `/api/v1/webhooks/endpoints/{id}/` | `webhooks:manage` | Remove an endpoint |
| GET | `/api/v1/webhooks/deliveries/` | `webhooks:manage` | Delivery log. Filter: `?status=failed` |

### API client management (admin only)

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/v1/clients/` | `webhooks:manage` | List clients |
| POST | `/api/v1/clients/` | `webhooks:manage` | Create a client — returns `plain_key` once |
| GET | `/api/v1/clients/{id}/` | `webhooks:manage` | One client |
| POST | `/api/v1/clients/{id}/revoke/` | `webhooks:manage` | Deactivate a key |

---

## 5. Outbound webhooks (you subscribe)

When a business event fires inside the system, we POST a signed JSON payload to every registered
endpoint that subscribes to that event type.

### Registering an endpoint

```bash
curl -X POST https://school.example.com/api/v1/webhooks/endpoints/ \
  -H "Authorization: Token sk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "name": "EMIS Portal",
    "url": "https://emis.example.gov/hooks/school",
    "events": ["student.enrolled", "result.published"]
  }'
```

The response includes a `secret` field. Store it — your server will use it to verify every
incoming request.

### Available event types

| Event | Fires when |
|---|---|
| `student.enrolled` | A new student record is created |
| `student.updated` | A student's record is updated |
| `student.status_changed` | Status changes (active → withdrawn, etc.) |
| `student.promoted` | A student is promoted to the next class |
| `academic_year.created` | A new academic year is added |
| `term.started` | A term is marked as current |
| `result.published` | A result sheet is published |
| `invoice.created` | A fee invoice is generated |
| `payment.received` | A payment is recorded |
| `payment.reversed` | A payment reversal is posted |
| `attendance.recorded` | An attendance register is completed |

Use `["*"]` as the events list to subscribe to all types.

### Payload format

Every POST body has this shape:

```json
{
  "event": "payment.received",
  "timestamp": "2026-11-26T10:30:00+03:00",
  "data": {
    "student": "GA/26/001",
    "amount": "1200.00"
  }
}
```

### Verifying signatures

Every request includes the header:

```
X-Webhook-Signature: sha256=<hex-digest>
X-Webhook-Event: payment.received
X-Delivery-ID: <uuid>
```

Verify by computing `HMAC-SHA256(raw_body, your_secret)` and comparing with
`hmac.compare_digest()`. **Always verify before processing.**

Python example:

```python
import hashlib, hmac

def verify(body: bytes, secret: str, header: str) -> bool:
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])
```

### Delivery behaviour

- Deliveries are attempted asynchronously via `django-q2` background workers.
- If the endpoint returns a non-2xx status or times out (15 s), the delivery is retried.
- Retry schedule: **5 min → 15 min → 45 min → 135 min** (exponential back-off).
- After **5 consecutive failures** the delivery is marked `abandoned`.
- After **10 consecutive failures** across any deliveries, the endpoint is auto-disabled.
  An admin can re-enable it via `/django-admin/api/webhookendpoint/`.
- The retry sweep (`retry_failed_webhooks`) must be scheduled as a django-q2 task running
  every 5 minutes (see [§9](#9-wiring-a-new-integration)).

---

## 6. Inbound webhooks (you push to us)

External systems — typically payment gateways — POST events to:

```
POST /api/v1/inbound/<source_slug>/
```

### Registering an inbound source

Add the source to `settings.py`:

```python
INBOUND_WEBHOOK_SOURCES = {
    "paystack": {
        "secret": env("PAYSTACK_WEBHOOK_SECRET"),
        "handler": "apps.api.inbound.handle_payment_gateway_webhook",
    },
    "emis": {
        "secret": env("EMIS_WEBHOOK_SECRET"),
        "handler": "apps.api.inbound.handle_emis_webhook",
    },
}
```

### Security

The sender must include:

```
X-Webhook-Signature: sha256=<hmac-sha256-of-raw-body>
```

(GitHub-style `X-Hub-Signature-256` is also accepted.) Requests without a valid signature
receive a **401** with no detail — we never reveal why verification failed.

### Processing model

1. Signature is verified immediately.
2. HTTP **200** is returned right away — the sender gets a fast ACK.
3. The actual work is queued via `django-q2` and runs in a background worker.

This prevents duplicate events from timed-out retries and keeps the sender's timeout budget free.

### Writing a custom handler

```python
# apps/api/inbound.py (or your own module)
def handle_my_gateway(source_slug: str, payload: dict) -> None:
    event = payload.get("event")
    data = payload.get("data", {})
    # ... process, call service layer, never direct ORM writes on finance models
```

Wire it in `INBOUND_WEBHOOK_SOURCES` and the platform will call it for every verified POST.

---

## 7. Error format

Every error from the API uses the same envelope:

```json
{
  "error": {
    "code": "authentication_failed",
    "message": "Invalid or revoked API key.",
    "detail": null
  }
}
```

For validation errors, `detail` is a dict keyed by field:

```json
{
  "error": {
    "code": "invalid",
    "message": "Validation failed.",
    "detail": {
      "school_class": ["Class not found for the given academic year."]
    }
  }
}
```

### Common error codes

| HTTP | Code | Meaning |
|---|---|---|
| 401 | `authentication_failed` | Missing, invalid or revoked API key |
| 403 | `permission_denied` | Valid key but missing required scope |
| 404 | `not_found` | Resource does not exist |
| 429 | `throttled` | Rate limit exceeded — see [§8](#8-rate-limiting) |
| 400 | `invalid` | Validation errors in the request body |

---

## 8. Rate limiting

| Caller type | Limit |
|---|---|
| General API requests | 1,000 per hour per client |
| Inbound webhook endpoints | 200 per minute |
| Auth (JWT obtain/refresh) | 20 per minute |

Exceeding a limit returns HTTP 429. The `Retry-After` header indicates when the window resets.

---

## 9. Wiring a new integration

### Step 1 — Create an API key

In Django admin at `/django-admin/api/apiclient/add/`, or via the API:

```bash
curl -X POST https://school.example.com/api/v1/clients/ \
  -H "Authorization: Token sk_<admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Integration",
    "scopes": ["students:read", "results:read"],
    "description": "LMS sync"
  }'
```

The response includes `plain_key`. **Copy it now** — it cannot be retrieved again.

### Step 2 — Register a webhook endpoint (optional)

```bash
curl -X POST https://school.example.com/api/v1/webhooks/endpoints/ \
  -H "Authorization: Token sk_<admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My LMS",
    "url": "https://lms.example.com/hooks/school",
    "events": ["student.enrolled", "result.published"]
  }'
```

### Step 3 — Dispatch events from service code

In the relevant service function, add a `dispatch_event` call after the action completes:

```python
# Example: apps/students/services.py — after enrolling a student
from apps.api.webhooks import dispatch_event

def enroll_student(...):
    student = Student.objects.create(...)
    # ... existing logic ...
    dispatch_event("student.enrolled", {
        "admission_number": student.admission_number,
        "full_name": student.user.full_name,
        "current_class": str(student.current_class),
    })
    return student
```

### Step 4 — Schedule the retry sweep

Run once during setup (or add to a migration / management command):

```python
from django_q.models import Schedule

Schedule.objects.get_or_create(
    name="Webhook retry sweep",
    defaults=dict(
        func="apps.api.webhooks.retry_failed_webhooks",
        schedule_type=Schedule.MINUTES,
        minutes=5,
    ),
)
```

### Step 5 — Register an inbound source (optional)

Add to `.env` and `settings.py` as described in [§6](#6-inbound-webhooks-you-push-to-us).

---

## 10. Configuration reference

New environment variables introduced by the API layer:

| Variable | Required | Notes |
|---|---|---|
| `INBOUND_WEBHOOK_SOURCES` | No | Dict in `settings.py` mapping slug → `{secret, handler}`. Not an env var — set in code. |

No other new environment variables are required. The API layer reads from the same `SECRET_KEY`,
`DATABASE_URL`, and `CACHE_URL` as the rest of the system.

**New `INSTALLED_APPS` entries** (added to `base.py`):
- `rest_framework_simplejwt`
- `rest_framework_simplejwt.token_blacklist`
- `django_filters`
- `drf_spectacular`
- `apps.api`

---

## 11. Known limitations

- **`students:write` is defined but not yet exposed.** Student creation goes through the admin
  console. The scope exists so clients can be pre-provisioned for when write endpoints are added.
- **Finance write endpoints are not yet built.** `finance:write` is used only by the inbound webhook
  handler (`handle_payment_gateway_webhook`), which currently logs and does nothing until
  `apps.finance.services.confirm_external_payment()` is implemented.
- **`dispatch_event()` must be called explicitly from service layer code.** No events fire
  automatically yet — the calls need to be added to each service function as integrations are
  built. See [§9](#9-wiring-a-new-integration) for the pattern.
- **The retry sweep is not automatically scheduled.** A one-time setup step is required (§9 Step 4).
- **Inbound sources are configured in code/settings, not the database.** There is no admin UI for
  adding inbound webhook sources — they must be declared in `settings.py`.
