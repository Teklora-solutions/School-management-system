"""
A single counter that invalidates every knowledge cache after a change.

Cached rankings and answers hold passage ids and answer text only. They are
still stamped with this counter so that approving, archiving, editing or
re-reading a document — or changing the embedding model — can never leave a
stale answer in place. It is a counter, not a timestamp, so two changes in the
same second cannot collide.
"""

from __future__ import annotations

from django.core.cache import cache

VERSION_KEY = "assistant:knowledge:version"
DEFAULT_VERSION = 1


def current() -> int:
    value = cache.get(VERSION_KEY)
    if value is None:
        cache.set(VERSION_KEY, DEFAULT_VERSION, None)
        return DEFAULT_VERSION
    return int(value)


def bump() -> None:
    if cache.add(VERSION_KEY, DEFAULT_VERSION, None):
        return
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, current() + 1, None)
