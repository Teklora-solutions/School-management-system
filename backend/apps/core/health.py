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

from django.core.cache import cache
from django.db import OperationalError, connection
from django.http import JsonResponse
from django.views import View


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
    Returns 200 {"status": "ok", "checks": {...}} or
            503 {"status": "error", "checks": {...}, "detail": "..."}
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

        if errors:
            return JsonResponse(
                {"status": "error", "checks": checks, "detail": "; ".join(errors)},
                status=503,
            )
        return JsonResponse({"status": "ok", "checks": checks})


# Keep backward-compatible alias so existing /healthz/ URL still works
HealthCheckView = ReadinessView
