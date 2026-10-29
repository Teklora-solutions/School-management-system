"""
apps/dashboards/cache.py
-------------------------
Thin cache wrapper around the heavy director KPI selector.

Cache strategy:
  - Key:    "director_kpis_term_{term.pk}"
  - TTL:    DIRECTOR_KPI_CACHE_TTL (default 10 minutes)
  - Stored: the dict returned by school_kpis() with a 'cached_at' timestamp
  - Invalidated explicitly by calling invalidate_school_kpis(term) whenever:
      * A ResultSheet transitions to PUBLISHED
      * A Payment is recorded or reversed

The invalidation hooks are called from the relevant service layers
(results/services.py for publish, finance/services.py for payments).
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

DIRECTOR_KPI_CACHE_TTL: int = getattr(settings, "DIRECTOR_KPI_CACHE_TTL", 600)  # 10 min


def _kpi_cache_key(term_id: int) -> str:
    return f"director_kpis_term_{term_id}"


def get_cached_school_kpis(*, term) -> dict:
    """
    Return cached KPIs for the given term, computing and storing them on miss.
    The returned dict always contains a 'cached_at' datetime key.
    """
    key = _kpi_cache_key(term.pk)
    data = cache.get(key)
    if data is not None:
        return data

    from .selectors import school_kpis

    data = school_kpis(term=term)
    data["cached_at"] = timezone.now()
    cache.set(key, data, DIRECTOR_KPI_CACHE_TTL)
    return data


def invalidate_school_kpis(*, term_id: int) -> None:
    """
    Explicitly evict the KPI cache for a term.
    Call this after any event that would change the KPI numbers.
    """
    cache.delete(_kpi_cache_key(term_id))


def invalidate_all_kpi_caches() -> None:
    """Evict KPI caches for every term (use sparingly)."""
    from apps.academics.models import Term

    for pk in Term.objects.values_list("pk", flat=True):
        cache.delete(_kpi_cache_key(pk))
