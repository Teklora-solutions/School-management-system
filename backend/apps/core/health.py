"""
apps/core/health.py
-------------------
Health and readiness probes for container/load-balancer checks.

  GET /healthz/  — liveness probe: is the process running?
                   Returns 200 {"status": "ok"} always.

  GET /readyz/   — readiness probe: can the process serve traffic?
                   Checks DB + cache. Returns 200 or 503.

Both endpoints are unauthenticated and excluded from axes rate-limiting.
"""

from __future__ import annotations

import time
from datetime import timedelta

from django.core.cache import cache
from django.db import OperationalError, connection
from django.http import JsonResponse
from django.utils import timezone
from django.views import View

#: The heartbeat task is scheduled every five minutes; three missed runs means
#: the worker has stopped (deploys and restarts can skip one).
WORKER_STALE_AFTER = timedelta(minutes=15)


def _worker_check() -> str:
    """
    Report the background worker's last heartbeat.

    This never contributes to ``errors``: a worker that has stopped is a
    problem to fix, but the site itself still serves every page, so the
    readiness probe must stay 200 and the load balancer must keep it.
    """
    from apps.core.models import WorkerHeartbeat

    try:
        last_run = WorkerHeartbeat.objects.values_list(
            "last_run_at", flat=True
        ).first()
    except Exception:  # noqa: BLE001 — an unmigrated table must not fail readiness
        return "unknown (heartbeat table unavailable)"

    if last_run is None:
        return "never"
    age = timezone.now() - last_run
    minutes = int(age.total_seconds() // 60)
    state = "ok" if age <= WORKER_STALE_AFTER else "stale"
    if minutes < 1:
        return f"{state} (last run just now)"
    return f"{state} (last run {minutes} min ago)"


class LivenessView(View):
    """
    Liveness probe — answers "is the process alive?"
    Always returns 200 as long as the Django process is running.
    Does NOT check DB or cache (those belong in readiness).
    """

    def get(self, request):
        return JsonResponse({"status": "ok"})


class ReadinessView(View):
    """
    Readiness probe — answers "can this process serve traffic?"
    Checks:
      - DB: execute a trivial query
      - Cache: set + get a key
      - Worker: report the background worker's last heartbeat (advisory only)
    Returns 200 {"status": "ok", "checks": {...}} or
            503 {"status": "error", "checks": {...}, "detail": "..."}
    The worker line never changes the status: a stopped worker must not take
    the website down with it.
    """

    def get(self, request):
        checks: dict[str, str] = {}
        errors: list[str] = []

        # ── Database ──────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["db"] = f"ok ({(time.monotonic() - t0) * 1000:.1f}ms)"
        except OperationalError as exc:
            checks["db"] = "error"
            errors.append(f"db: {exc}")

        # ── Cache ─────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            _key = "_readyz_probe"
            cache.set(_key, "1", timeout=5)
            val = cache.get(_key)
            if val != "1":
                raise RuntimeError("cache set/get mismatch")
            checks["cache"] = f"ok ({(time.monotonic() - t0) * 1000:.1f}ms)"
        except Exception as exc:  # noqa: BLE001
            checks["cache"] = "error"
            errors.append(f"cache: {exc}")

        # ── Background worker ─────────────────────────────────────────────
        # Reported, never fatal: see ``_worker_check``.
        checks["worker"] = _worker_check()

        if errors:
            return JsonResponse(
                {"status": "error", "checks": checks, "detail": "; ".join(errors)},
                status=503,
            )
        return JsonResponse({"status": "ok", "checks": checks})


# Keep backward-compatible alias so existing /healthz/ URL still works
HealthCheckView = ReadinessView
