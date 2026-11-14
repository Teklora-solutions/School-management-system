"""
Splitting a plain-text post body into text and resolved @mention segments.

The template loops over the segments and autoescapes each one; nothing here
produces HTML, so a body can never inject markup through a mention.
"""

from __future__ import annotations

import re


def body_segments(body: str, mentions) -> list[dict]:
    body = body or ""
    names = [row.get("name") for row in (mentions or []) if row.get("name")]
    if not body:
        return []
    if not names:
        return [{"text": body, "mention": False}]

    names.sort(key=len, reverse=True)
    markers = {"@" + name.lower() for name in names}
    pattern = re.compile(
        "(" + "|".join("@" + re.escape(name) for name in names) + ")",
        re.IGNORECASE,
    )
    segments = []
    for part in pattern.split(body):
        if not part:
            continue
        segments.append({"text": part, "mention": part.lower() in markers})
    return segments
