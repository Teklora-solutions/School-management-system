"""
A personal-data scan for knowledge documents, run before any passage is sent
to an embedding provider.

Detects email addresses, Kenyan phone numbers, ID/admission-like numbers, and
likely person names next to pupil/parent words. Findings record the kind and
where it was seen (heading and passage number) plus a masked snippet. The
matching value itself is never stored here.

It is a heuristic, not a named-entity model. It will miss unusual spellings and
Sheng nicknames, and it may flag a harmless number. That is why the admin
confirms or edits the document rather than trusting the scan silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..guardrails.pii import (
    ADMISSION_PATH_RE,
    EMAIL_RE,
    LABELLED_ID_RE,
    PHONE_RE,
)

CONFIRMATION_TEXT = (
    "This document contains no personal data of pupils, parents or staff, or I "
    "accept that it will be sent to the selected provider"
)

LONG_NUMBER_RE = re.compile(r"\b\d{7,}\b")
CODE_ID_RE = re.compile(r"\b[A-Z]{2,5}[-/]\d{2,6}\b")
NAME_CONTEXT_RE = re.compile(
    r"(?i)\b(?:pupil|student|learner|parent|guardian|mtoto|mwanafunzi|mzazi)\b"
    r"[^.\n]{0,25}?:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"
)
NAME_PHRASE_RE = re.compile(
    r"(?i)\b(?:named|called|known\s+as|jina(?:\s+lake)?\s+ni)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"
)

TOKENS = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "id": "[ID]",
    "name": "[NAME]",
}

PATTERNS = (
    ("email", EMAIL_RE),
    ("phone", PHONE_RE),
    ("id", LABELLED_ID_RE),
    ("id", ADMISSION_PATH_RE),
    ("id", CODE_ID_RE),
    ("id", LONG_NUMBER_RE),
    ("name", NAME_CONTEXT_RE),
    ("name", NAME_PHRASE_RE),
)


@dataclass(frozen=True)
class Finding:
    kind: str
    heading: str
    passage: int
    snippet: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "heading": self.heading[:300],
            "passage": self.passage,
            "snippet": self.snippet[:160],
        }


def _snippet(text: str, start: int, end: int, spans) -> str:
    """A window around one finding, with every other finding in it masked too.

    ``spans`` are the match positions found on the full text. They are reused
    rather than re-running the patterns on the window, because a cut window can
    lose the trigger word ("Pupil:") and let a name through.
    """
    left = max(0, start - 35)
    right = min(len(text), end + 35)
    parts: list[str] = ["…"]
    cursor = left
    for span_start, span_end, kind in sorted(spans):
        if span_end <= left or span_start >= right:
            continue
        span_start = max(span_start, cursor)
        span_end = min(span_end, right)
        if span_end <= span_start:
            continue
        parts.append(text[cursor:span_start])
        parts.append(TOKENS[kind])
        cursor = span_end
    parts.append(text[cursor:right])
    parts.append("…")
    return "".join(parts).strip()


def scan_text(text: str, *, heading: str = "", passage: int = 0) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    spans = [
        (match.start(), match.end(), kind)
        for kind, pattern in PATTERNS
        for match in pattern.finditer(text or "")
    ]
    for start, end, kind in spans:
        snippet = _snippet(text, start, end, spans)
        key = (kind, snippet)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            Finding(kind=kind, heading=heading or "", passage=passage, snippet=snippet)
        )
    return findings


def scan_passages(passages) -> list[Finding]:
    """``passages`` is a list of objects with ``text`` and ``heading``."""
    findings: list[Finding] = []
    for index, passage in enumerate(passages):
        findings.extend(
            scan_text(passage.text, heading=getattr(passage, "heading", ""), passage=index)
        )
    return findings


def record_scan(document, findings: list[Finding]) -> None:
    """Store the state and the found locations. Never stores the value itself."""
    from django.utils import timezone

    from ..models import KnowledgeDocument

    document.pii_findings = [finding.as_dict() for finding in findings]
    document.pii_scanned_at = timezone.now()
    if findings and document.pii_confirmed_at is None:
        document.pii_state = KnowledgeDocument.PIIState.FOUND
    elif document.pii_confirmed_at is not None:
        document.pii_state = KnowledgeDocument.PIIState.CONFIRMED
    else:
        document.pii_state = KnowledgeDocument.PIIState.CLEAN
    document.save(
        update_fields=["pii_findings", "pii_scanned_at", "pii_state", "updated_at"]
    )


def describe(findings: list[Finding]) -> str:
    kinds = sorted({finding.kind for finding in findings})
    labels = {"email": "email address", "phone": "phone number", "id": "ID number", "name": "person name"}
    return ", ".join(labels.get(kind, kind) for kind in kinds)
