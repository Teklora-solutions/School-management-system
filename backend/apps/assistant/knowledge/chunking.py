"""
Split readable text into passages an assistant can cite.

Rules
  About 800 characters per passage, with about 120 characters of overlap so a
  sentence cut at a boundary is still whole in one of the passages.
  Heading lines (``#`` to ``######``) start a new section; every passage keeps
  the heading path it sits under, e.g. "Fees › Payment plans".
  Fragments under 40 characters are dropped.
  More than 60 passages is refused with plain wording: a single huge document
  is split by the admin rather than silently truncated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNK_TARGET = 800
CHUNK_OVERLAP = 120
MIN_CHUNK = 40
MAX_CHUNKS = 60
MAX_HEADING = 300
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


class ChunkingError(Exception):
    def __init__(self, message: str) -> None:
        self.safe_message = message
        super().__init__(message)


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str = ""


def split_text(text: str) -> list[Chunk]:
    """Return the passages of ``text``. Raises ``ChunkingError``."""
    normalised = _normalise(text)
    chunks: list[Chunk] = []
    for heading, body in _sections(normalised):
        chunks.extend(_section_chunks(heading[:MAX_HEADING], body))

    if len(chunks) > MAX_CHUNKS:
        raise ChunkingError(
            "This document is too long to read in one go (more than "
            f"{MAX_CHUNKS} passages). Split it into smaller documents."
        )
    if not chunks:
        raise ChunkingError("This document has too little text to answer from.")
    return chunks


def _normalise(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines.append(re.sub(r"[ \t\f\v]+", " ", line).strip())
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if line:
            collapsed.append(line)
            blank = False
        elif not blank:
            collapsed.append("")
            blank = True
    return "\n".join(collapsed).strip()


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading path, body) pairs. Text before the first heading has no heading."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            sections.append((heading, joined))
        body.clear()

    for line in text.split("\n"):
        match = HEADING_RE.match(line)
        if not match:
            body.append(line)
            continue
        flush()
        level = len(match.group(1))
        stack = [(old_level, title) for old_level, title in stack if old_level < level]
        stack.append((level, match.group(2).strip()))
        heading = " › ".join(title for _, title in stack)

    flush()
    return sections


def _section_chunks(heading: str, body: str) -> list[Chunk]:
    body = body.strip()
    if not body:
        return []
    if len(body) <= CHUNK_TARGET:
        return [Chunk(body, heading)] if len(body) >= MIN_CHUNK else []

    chunks: list[Chunk] = []
    start = 0
    length = len(body)
    while start < length:
        end = min(start + CHUNK_TARGET, length)
        if end < length:
            end = _break_point(body, start, end)
        piece = body[start:end].strip()
        if len(piece) >= MIN_CHUNK:
            chunks.append(Chunk(piece, heading))
        if end >= length:
            break
        next_start = max(end - CHUNK_OVERLAP, start + 1)
        while next_start < end and not body[next_start - 1].isspace():
            next_start += 1
        start = next_start if next_start > start else end
    return chunks


def _break_point(text: str, start: int, end: int) -> int:
    """Prefer a paragraph, then a line, then a sentence, then a word boundary."""
    floor = max(start + MIN_CHUNK, end - 200)
    window = text[floor:end]
    for marker in ("\n\n", "\n", ". ", "? ", "! ", "; "):
        found = window.rfind(marker)
        if found != -1:
            return floor + found + len(marker)
    found = window.rfind(" ")
    if found > 0:
        return floor + found + 1
    return end
