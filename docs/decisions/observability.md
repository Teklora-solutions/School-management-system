# ADR-004: Observability Strategy

**Status:** Accepted  
**Date:** 2026-10-16  
**Deciders:** Engineering team

---

## Context

A school management system handling fees, results, and student records must be
observable in production without requiring a dedicated DevOps team.  The school
runs on a single Linux server (no Kubernetes, no managed log aggregation).  Key
requirements:

- Every HTTP request must be traceable by a unique ID across logs.
- Slow requests (> 500 ms) must be visible without manual log scraping.
- Critical business events (payment posted, results published) must be logged
  separately from application noise so finance staff can audit them.
- Errors must surface immediately — the admin should not find out about a crash
  from a teacher complaint.
- The solution must have zero cost for a small school, with a clear upgrade path.

---

## Decision

### 1. Request tracing — `X-Request-ID` header + thread-local

`RequestIDMiddleware` (position 10 in `MIDDLEWARE`) generates a UUID for every
request, stores it on `request.request_id` and in `threading.local()`.  The ID
is:

- Returned in the `X-Request-ID` response header (for client-side correlation)
- Injected into every log record via `extra={"request_id": ...}`
- Stored on `AuditLog.request_id` by `apps.core.audit_log.log_action()`

Thread-local is explicitly cleared after each response to prevent leakage
across WSGI worker requests.

### 2. Structured JSON logging — `python-json-logger`

`base.py` uses `pythonjsonlogger.jsonlogger.JsonFormatter` in production
(`LOG_FORMAT=json`).  Every log record is a single-line JSON object:

```json
{
  "timestamp": "2026-10-16T08:32:11.412Z",
  "level": "WARNING",
  "logger": "apps.core.middleware",
  "message": "slow_request",
  "event": "http_request",
  "request_id": "a1b2c3d4-...",
  "user_id": 42,
  "method": "GET",
  "path": "/dashboards/director/",
  "status_code": 200,
  "elapsed_ms": 680.3
}
```

In development `LOG_FORMAT=verbose` (the default) produces human-readable lines.

### 3. Slow-request logging

`RequestTimingMiddleware` emits a `WARNING` record for any request exceeding
`SLOW_REQUEST_THRESHOLD_MS = 500`.  The record includes `path`, `method`,
`elapsed_ms`, `user_id`, and `request_id`.

In production, alert on `level=WARNING AND event=http_request AND elapsed_ms > 500`
using a simple `grep` pipeline or the log-routing tool of choice (Loki, Fluentd).

### 4. Business-events log — `apps.events` logger

`apps.core.business_events.log_event()` routes to the `apps.events` Python
logger at `INFO` level.  Events emitted:

| Event | Triggered by |
|---|---|
| `payment_posted` | `finance.services.record_payment()` |
| `payment_reversed` | `finance.services.reverse_payment()` |
| `invoices_posted` | `finance.services.post_invoices()` |
| `results_published` | `results.services.publish_sheet()` |

The `apps.events` logger is configured separately in `LOGGING` so it can be
routed to a dedicated file or sink without changing application code:

```python
# In prod.py or a local settings override:
LOGGING["handlers"]["events_file"] = {
    "class": "logging.handlers.TimedRotatingFileHandler",
    "filename": "/var/log/school/events.jsonl",
    "when": "midnight",
    "backupCount": 90,
    "formatter": "json",
}
LOGGING["loggers"]["apps.events"]["handlers"] = ["events_file"]
```

### 5. Sentry error tracking

`prod.py` initialises `sentry_sdk` when `SENTRY_DSN` is set (free tier is
sufficient for a small school).  Integrations:

- `DjangoIntegration` — captures unhandled exceptions with full request context,
  tags transactions by URL pattern, adds middleware spans.
- `LoggingIntegration` — any `logging.ERROR` record is also sent to Sentry as
  an event, even if the exception was caught.

`send_default_pii=False` ensures email addresses and request bodies are never
transmitted to Sentry.

`SENTRY_RELEASE` is set from `$GIT_SHA` in the CI workflow so each deployment
creates a new release in Sentry for regression tracking.

---

## Alternatives considered

| Option | Rejected reason |
|---|---|
| django-silk | Good for dev profiling, too much overhead for production |
| Datadog / New Relic APM | Cost; overkill for a single-server school app |
| Custom audit table for HTTP logs | DB write on every request is too expensive |
| ELK stack | Infrastructure complexity exceeds school's ops capacity |

---

## Consequences

- Every log line in production is valid JSON → can be piped to `jq` for ad-hoc queries.
- Request ID links HTTP logs to audit logs to Sentry events.
- Finance staff can `grep '"event":"payment_posted"'` on the events log to audit all payments.
- Sentry free tier (5 k events/month) is sufficient given the school's scale.
- `python-json-logger` is a lightweight dependency (no C extensions).
- Adding a new business event requires only one `log_event(...)` call — no schema changes.
