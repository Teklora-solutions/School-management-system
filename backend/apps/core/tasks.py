"""
apps/core/tasks.py
------------------
Small functions run by the background worker rather than a request.

They live here rather than in a view because django-q calls them by dotted
path from a ``django_q.Schedule`` row, where a failed import is a job that
silently never runs.
"""

from __future__ import annotations

import logging

from .models import WorkerHeartbeat

logger = logging.getLogger(__name__)


def worker_heartbeat() -> str:
    """Record that the queue is being served; scheduled every five minutes."""
    ran_at = WorkerHeartbeat.record()
    logger.info("Worker heartbeat recorded at %s", ran_at.isoformat())
    return ran_at.isoformat()
