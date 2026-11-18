"""
Template tag that decides whether a public page carries the chat widget.

Public pages are read by everyone, so the answer is memoised for a minute and
cleared whenever ``AssistantSettings`` is saved. A school with the widget
switched off (the default) ships no widget markup, no stylesheet and no script
to any visitor.
"""

from __future__ import annotations

from django import template
from django.core.cache import cache

from ..chat import widget_mounted
from ..models import WIDGET_GATE_CACHE_KEY, WIDGET_GATE_SECONDS, AssistantSettings

register = template.Library()


@register.simple_tag
def widget_available() -> bool:
    ready = cache.get(WIDGET_GATE_CACHE_KEY)
    if ready is None:
        ready = widget_mounted(AssistantSettings.read())
        cache.set(WIDGET_GATE_CACHE_KEY, ready, timeout=WIDGET_GATE_SECONDS)
    return bool(ready)
