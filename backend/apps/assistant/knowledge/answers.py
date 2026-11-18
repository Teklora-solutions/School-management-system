"""
Answering from approved knowledge.

The order is fixed and cannot be changed by a setting:

  1. safeguarding screening — a hit stops everything and no provider is called;
  2. retrieval over only what this audience may see, hybrid vector + keyword;
  3. a weak result is refused before a provider is asked to invent an answer;
  4. the model is told the sources are data and must cite [S1], [S2] …;
  5. citations are checked — unknown markers are stripped;
  6. every figure (number, amount, date, time) in the answer must appear in the
     text of the passages it cited;
  7. anything that fails is marked unverified and never shown to a reader.

Honest limit of the figure check: it can show that a cited figure exists in the
cited text. It cannot show that the sentence around it is a fair summary, or
that a claim without a figure is supported. An answer can pass with a true
figure used for the wrong point. It is a guard against invented money and
dates, not a proof of correctness.

The fixed refusal wording below is DRAFT and awaits the school's approval.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace

from django.conf import settings as django_settings
from django.core.cache import cache

from .. import gateway, selectors
from ..guardrails import build_safe_reply, mask_text, screen
from ..guardrails.injection import MAX_QUESTION_CHARS, build_messages
from ..models import AssistantSettings, KnowledgeGap, UsageEvent
from ..providers import registry
from . import retrieval, scope, versions

# The refusal is shown to a visitor or signed-in reader but is NOT final until
# the school approves the wording. The school contact comes first in both
# languages, as instructed.
REFUSAL_EN = (
    "Please contact the {school} office{reach} — they can help with this.\n"
    "I don't have that in the school's approved information, so I won't guess."
)
REFUSAL_SW = (
    "Tafadhali wasiliana na ofisi ya shule{reach} — wanaweza kukusaidia.\n"
    "Sina jibu hilo katika taarifa zilizoidhinishwa za shule, kwa hivyo sitakisia."
)

ANSWER_TTL = 60 * 60
MAX_ANSWER_CHARS = 4000

SWAHILI_HINTS = frozenset(
    {
        "na", "ya", "wa", "kwa", "ni", "za", "la", "katika", "shule", "ada",
        "bei", "wanafunzi", "mtoto", "mzazi", "mwaka", "darasa", "nini", "lini",
        "wapi", "kiasi", "gani", "je", "tafadhali", "asante", "habari",
    }
)

CITATION_RE = re.compile(r"\[S(\d+)\]", re.IGNORECASE)
NUMBER_RE = re.compile(r"(?<![\d.])\d[\d,]*(?:\.\d+)?(?![\d])")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")


def refusal_text(settings_row=None) -> str:
    school = getattr(django_settings, "SCHOOL_NAME", "the school")
    reach = ""
    email = (getattr(settings_row, "handoff_email", "") or "").strip() if settings_row else ""
    if email:
        reach = f" ({email})"
    return (
        REFUSAL_EN.format(school=school, reach=reach)
        + "\n\n"
        + REFUSAL_SW.format(reach=reach)
    )


def language_hint(question: str) -> str:
    words = set(re.findall(r"[a-z]+", (question or "").lower()))
    return "sw" if len(words & SWAHILI_HINTS) >= 2 else "en"


@dataclass(frozen=True)
class PromptSource:
    id: str
    title: str
    text: str


@dataclass
class Answer:
    question: str
    masked_question: str
    audience_label: str = ""
    mask_counts: dict[str, int] = field(default_factory=dict)

    screened: bool = False
    rules_fired: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    safe_reply: str = ""
    alert_preview: dict = field(default_factory=dict)

    refused: bool = False
    refusal_text: str = ""
    sources: list[retrieval.Source] = field(default_factory=list)
    top_vector: float = 0.0
    top_keyword: float = 0.0

    answer: str = ""
    raw_answer: str = ""
    citations: list[dict] = field(default_factory=list)
    unknown_citations: int = 0
    citation_missing: bool = False
    figure_problems: list[str] = field(default_factory=list)
    verified: bool = False

    provider_name: str = ""
    model: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    provider_called: bool = False
    error: str = ""
    from_cache: bool = False

    gap_id: int | None = None
    language: str = "en"

    @property
    def status_label(self) -> str:
        if self.screened:
            return "Safeguarding — passed to a person"
        if self.error:
            return "No answer — provider problem"
        if self.refused:
            return "Refused — nothing reliable found"
        if self.verified:
            return "Verified"
        return "Unverified — not shown to a reader"

    @property
    def delivered_to_reader(self) -> str:
        """Exactly what a visitor or signed-in person would receive."""
        if self.screened:
            return self.safe_reply
        if self.verified and self.answer.strip():
            return self.answer
        return self.refusal_text


def deliver(answer: Answer, settings_row=None) -> str:
    """
    The only function a user-facing screen may use. It never returns an
    unverified answer; the fixed refusal is used instead.
    """
    if answer.screened:
        return answer.safe_reply
    if answer.verified and answer.answer.strip():
        return answer.answer
    return refusal_text(settings_row or AssistantSettings.read())


def clean_citations(text: str, source_count: int) -> tuple[str, list[int], int]:
    """Strip unknown citation markers and return the valid source numbers."""
    valid: list[int] = []
    unknown = 0

    def replace(match):
        nonlocal unknown
        number = int(match.group(1))
        if 1 <= number <= source_count:
            if number not in valid:
                valid.append(number)
            return f"[S{number}]"
        unknown += 1
        return ""

    cleaned = CITATION_RE.sub(replace, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, valid, unknown


def _normalise_figures(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in NUMBER_RE.finditer(text or "")}


def figure_check(answer_text: str, cited_text: str) -> list[str]:
    """
    Every number, amount, date part and time part in the answer must appear in
    the text of the cited passages. List markers such as “1.” are ignored.
    """
    body = LIST_MARKER_RE.sub("", answer_text or "")
    body = CITATION_RE.sub("", body)
    wanted = _normalise_figures(body)
    available = _normalise_figures(cited_text)
    return sorted(figure for figure in wanted if figure not in available)


def _answer_cache_key(*, audience, question, collection_ids, model_signature) -> str:
    payload = "|".join(
        [
            audience.signature,
            ",".join(str(pk) for pk in collection_ids),
            hashlib.sha256(question.encode("utf-8")).hexdigest(),
            model_signature,
            str(versions.current()),
        ]
    )
    return f"assistant:answer:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _model_signature(providers) -> str:
    return ",".join(
        f"{provider.pk}:{registry.model_for(provider, 'embed')}"
        for provider in providers
        if registry.model_for(provider, "embed")
    )


def _cached_answer(key: str, audience, on_date=None) -> Answer | None:
    cached = cache.get(key)
    if not cached:
        return None

    if cached.get("refused"):
        return Answer(
            question="",
            masked_question="",
            audience_label=audience.label,
            refused=True,
            refusal_text=refusal_text(AssistantSettings.read()),
            top_vector=cached.get("top_vector", 0.0),
            top_keyword=cached.get("top_keyword", 0.0),
            from_cache=True,
            language=cached.get("language", "en"),
        )

    if not cached.get("verified"):
        return None

    cited_ids = [citation["chunk_id"] for citation in cached.get("citations", [])]
    # Read-time check: every cited passage must still be visible and inside its
    # effective dates. A document that has expired drops the whole cached answer.
    still_visible = retrieval.visible_chunk_map(audience, cited_ids, on_date=on_date)
    if len(still_visible) != len(set(cited_ids)):
        return None

    return Answer(
        question="",
        masked_question="",
        audience_label=audience.label,
        answer=cached.get("answer", ""),
        citations=[dict(citation) for citation in cached.get("citations", [])],
        verified=True,
        top_vector=cached.get("top_vector", 0.0),
        top_keyword=cached.get("top_keyword", 0.0),
        from_cache=True,
        language=cached.get("language", "en"),
    )


def _store_answer(key: str, answer: Answer) -> None:
    # The question is never cached: the asker's own wording is the one thing a
    # cache value has no need for, and it may carry text masking missed.
    cache.set(
        key,
        {
            "answer": answer.answer,
            "citations": [
                {
                    "label": citation["label"],
                    "document_id": citation["document_id"],
                    "title": citation["title"],
                    "chunk_id": citation["chunk_id"],
                }
                for citation in answer.citations
            ],
            "verified": answer.verified,
            "refused": answer.refused,
            "top_vector": answer.top_vector,
            "top_keyword": answer.top_keyword,
            "language": answer.language,
        },
        ANSWER_TTL,
    )


def record_gap(
    *,
    question: str,
    masked_question: str,
    audience,
    reason: str,
    top_score: float | None,
    is_test: bool = False,
) -> KnowledgeGap:
    return KnowledgeGap.objects.create(
        question=(masked_question or question)[:300],
        role=audience.role,
        class_signature=",".join(str(pk) for pk in sorted(audience.class_ids)),
        language_hint=language_hint(question),
        reason=reason,
        top_score=top_score,
        is_test=is_test,
    )


def answer_question(
    question: str,
    *,
    audience: scope.Audience,
    actor=None,
    profile: str = UsageEvent.Profile.CONSOLE,
    provider_id: int | None = None,
    use_cache: bool = True,
    record_gaps: bool = True,
    is_test: bool = False,
    on_date=None,
) -> Answer:
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    settings_row = AssistantSettings.read()
    masked = mask_text(question)
    language = language_hint(question)
    refusal = refusal_text(settings_row)

    answer = Answer(
        question=question,
        masked_question=masked.text,
        audience_label=audience.label,
        mask_counts=masked.counts,
        refusal_text=refusal,
        language=language,
    )

    screening = screen(question, selectors.custom_rules_active())
    if screening.triggered:
        answer.screened = True
        answer.rules_fired = screening.rule_ids
        answer.categories = screening.categories
        answer.safe_reply = build_safe_reply(settings_row)
        from .. import alerts

        answer.alert_preview = alerts.would_notify(settings_row)
        return answer

    providers = selectors.providers_in_order()
    collection_ids = scope.visible_collection_ids(audience, on_date)
    cache_key = _answer_cache_key(
        audience=audience,
        question=masked.text,
        collection_ids=collection_ids,
        model_signature=_model_signature(providers),
    )
    if use_cache:
        cached = _cached_answer(cache_key, audience, on_date)
        if cached is not None:
            cached.refusal_text = refusal
            return cached

    try:
        found = retrieval.search(
            masked.text,
            audience,
            profile=profile,
            actor=actor,
            on_date=on_date,
            use_cache=use_cache,
        )
    except retrieval.RetrievalUnavailable as exc:
        answer.error = exc.message
        return answer

    answer.sources = found.sources
    answer.top_vector = found.top_vector
    answer.top_keyword = found.top_keyword
    answer.model = found.model
    answer.refused = found.refused or not found.sources

    if answer.refused:
        if record_gaps:
            gap = record_gap(
                question=question,
                masked_question=masked.text,
                audience=audience,
                reason=KnowledgeGap.Reason.NO_MATCH,
                top_score=found.top_vector,
                is_test=is_test,
            )
            answer.gap_id = gap.pk
        if use_cache:
            _store_answer(cache_key, answer)
        return answer

    provider = None
    if provider_id:
        from ..models import ProviderConfig

        provider = ProviderConfig.objects.filter(pk=provider_id, is_active=True).first()
    if provider is None:
        provider = providers[0] if providers else None
    if provider is None:
        answer.error = "No active provider is configured. Add one before asking."
        return answer

    sources = [
        replace(source, label=f"S{index}")
        for index, source in enumerate(found.sources, start=1)
    ]
    answer.sources = sources
    prompt_sources = [
        PromptSource(id=source.label, title=source.document_title, text=source.text)
        for source in sources
    ]
    messages = build_messages(
        school_name=getattr(django_settings, "SCHOOL_NAME", "the school"),
        house_rules=settings_row.house_rules,
        question=masked.text,
        sources=prompt_sources,
    )

    try:
        result = gateway.call_chain(
            [provider],
            messages,
            actor=actor,
            profile=profile,
            max_tokens=400,
        )
    except gateway.AllProvidersUnavailable as exc:
        answer.error = getattr(exc, "detail", "") or str(exc)
        answer.provider_name = provider.name
        return answer

    answer.provider_name = provider.name
    answer.provider_called = True
    answer.latency_ms = result.latency_ms
    answer.tokens_in = result.reply.tokens_in
    answer.tokens_out = result.reply.tokens_out
    answer.raw_answer = result.reply.text[:MAX_ANSWER_CHARS]

    cleaned, cited_numbers, unknown = clean_citations(answer.raw_answer, len(sources))
    answer.answer = cleaned
    answer.unknown_citations = unknown
    answer.citation_missing = not cited_numbers

    cited_sources = [sources[number - 1] for number in cited_numbers]
    answer.citations = [
        {
            "label": source.label,
            "document_id": source.document_id,
            "title": source.document_title,
            "chunk_id": source.chunk_id,
        }
        for source in cited_sources
    ]

    if cited_numbers and cleaned.strip():
        cited_text = "\n".join(source.text for source in cited_sources)
        answer.figure_problems = figure_check(cleaned, cited_text)

    answer.verified = bool(
        cited_numbers
        and cleaned.strip()
        and not answer.figure_problems
    )

    if not answer.verified and record_gaps:
        gap = record_gap(
            question=question,
            masked_question=masked.text,
            audience=audience,
            reason=KnowledgeGap.Reason.UNVERIFIED,
            top_score=found.top_vector,
            is_test=is_test,
        )
        answer.gap_id = gap.pk

    if use_cache and answer.verified:
        _store_answer(cache_key, answer)
    return answer
