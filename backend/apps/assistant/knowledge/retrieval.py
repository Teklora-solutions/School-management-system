"""
Hybrid retrieval over the visible passages.

Two rankings are built from the same, already-scoped passages:

  * vector ranking — cosine similarity between the question embedding and each
    passage embedding;
  * keyword ranking — a simple weighted term frequency over the passage text,
    case and punctuation insensitive, splitting English and Kiswahili words the
    same way. Tokens that contain a digit (fee amounts, dates, class numbers)
    count three times, because an exact figure matters more than a vague match.

The two rankings are merged with reciprocal rank fusion (RRF), which only uses
each ranking's order, so a keyword hit can lift a passage the vectors missed —
for example a passage naming “Grade 4” and an exact term fee.

The scores are NOT comparable across embedding models; MIN_SCORE only means
something for the model in use. K4 calibrates it on the evaluation set with
real embeddings. Until then it is a conservative floor, not a tuned value.

Scope comes first: ``scope.scoped_documents`` is applied before any ranking, so
a passage the reader may not see can never influence a result. Cached rankings
store passage ids and scores only; every cached id is re-checked against the
scope and the effective dates at read time, which is how a document that has
passed its end date stops being used even if the cache still holds its id.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from django.core.cache import cache

from .. import gateway, selectors
from ..models import KnowledgeChunk, KnowledgeDocument, UsageEvent
from ..providers import registry
from . import scope, versions

# One place to change the retrieval knobs. K4 calibrates MIN_SCORE and the
# weighting on real embeddings; the fake provider's numbers mean nothing.
TOP_K = 5
MIN_SCORE = 0.30
MAX_QUESTION = 4000
RRF_K = 60
KEYWORD_DIGIT_WEIGHT = 3.0

RETRIEVAL_TTL = 60 * 10
QUESTION_EMBEDDING_TTL = 60 * 60 * 24 * 30

_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÿ']+")

# A short list in both languages. Removing these keeps the keyword ranking
# about the words that carry meaning, not grammar.
STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "have", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was",
        "were", "will", "with", "what", "when", "where", "who", "how", "much",
        "many", "i", "my", "we", "our", "you", "your",
        "na", "ya", "wa", "kwa", "ni", "za", "la", "katika", "hii", "hilo",
        "kuna", "je", "nini", "gani", "mimi", "wangu", "sisi", "yetu", "we",
    }
)


class RetrievalUnavailable(RuntimeError):
    """The question could not be embedded. ``message`` is safe to show."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Source:
    chunk_id: int
    document_id: int
    document_title: str
    ordinal: int
    heading: str
    text: str
    vector_score: float = 0.0
    keyword_score: float = 0.0
    combined_score: float = 0.0
    label: str = ""


@dataclass
class RetrievalResult:
    sources: list[Source] = field(default_factory=list)
    top_vector: float = 0.0
    top_keyword: float = 0.0
    model: str = ""
    dimension: int = 0
    refused: bool = False
    reason: str = ""
    embedded: bool = False
    from_cache: bool = False


def tokenise(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in _WORD_RE.finditer(text or "")]
    return [token for token in tokens if token not in STOPWORDS]


def keyword_score(query_tokens: list[str], text: str) -> float:
    counts = Counter(tokenise(text))
    score = 0.0
    for token in query_tokens:
        found = counts.get(token, 0)
        if not found:
            continue
        score += found * (KEYWORD_DIGIT_WEIGHT if any(ch.isdigit() for ch in token) else 1.0)
    return score


def cosine(first, second) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = 0.0
    first_norm = 0.0
    second_norm = 0.0
    for a, b in zip(first, second):
        try:
            a = float(a)
            b = float(b)
        except (TypeError, ValueError):
            return 0.0
        dot += a * b
        first_norm += a * a
        second_norm += b * b
    if first_norm <= 0 or second_norm <= 0:
        return 0.0
    return dot / (math.sqrt(first_norm) * math.sqrt(second_norm))


def merge_rankings(vector_ranks: list[int], keyword_ranks: list[int]) -> dict[int, float]:
    """Reciprocal rank fusion. Each list is cut down to its own order."""
    combined: dict[int, float] = {}
    for ranking in (vector_ranks, keyword_ranks):
        for position, chunk_id in enumerate(ranking, start=1):
            combined[chunk_id] = combined.get(chunk_id, 0.0) + 1.0 / (RRF_K + position)
    return combined


def _hash(*parts) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _model_signature(providers) -> str:
    parts = []
    for provider in providers:
        model = registry.model_for(provider, "embed")
        if model:
            parts.append(f"{provider.pk}:{model}")
    return ",".join(parts)


def _visible_chunk_rows(audience, on_date=None):
    visible = scope.scoped_documents(audience, on_date)
    queryset = (
        KnowledgeChunk.objects.filter(
            document__in=visible,
            document__ingestion_state=KnowledgeDocument.IngestionState.READY,
            document__is_stale=False,
        )
        .select_related("document")
        .order_by("pk")
    )
    return [chunk for chunk in queryset if chunk.embedding]


def visible_chunk_map(audience, chunk_ids, *, on_date=None, model: str = "") -> dict[int, KnowledgeChunk]:
    """Hydrate cached ids through the live scope, dropping anything no longer allowed."""
    if not chunk_ids:
        return {}
    visible = scope.scoped_documents(audience, on_date)
    queryset = (
        KnowledgeChunk.objects.filter(
            pk__in=list(chunk_ids),
            document__in=visible,
            document__ingestion_state=KnowledgeDocument.IngestionState.READY,
            document__is_stale=False,
        )
        .select_related("document")
    )
    if model:
        queryset = queryset.filter(embedding_model=model)
    return {chunk.pk: chunk for chunk in queryset if chunk.embedding}


def _question_embedding(query: str, providers, *, profile, actor) -> tuple[list[float], str, int]:
    signature = _model_signature(providers)
    key = f"assistant:qembed:{_hash(signature, query)}"
    cached = cache.get(key)
    if cached and cached.get("vector"):
        return cached["vector"], cached.get("model", ""), int(cached.get("dimension", 0))

    try:
        result = gateway.embed_chain(providers, [query], actor=actor, profile=profile)
    except gateway.AllProvidersUnavailable as exc:
        raise RetrievalUnavailable(getattr(exc, "detail", "") or str(exc)) from None

    vector = list(result.reply.vectors[0])
    model = (result.reply.model or registry.model_for(result.provider, "embed")).strip()
    dimension = int(result.reply.dimension or len(vector))
    cache.set(
        key,
        {"vector": vector, "model": model, "dimension": dimension},
        QUESTION_EMBEDDING_TTL,
    )
    return vector, model, dimension


def _source_from(chunk, *, vector_score, keyword_score, combined_score) -> Source:
    return Source(
        chunk_id=chunk.pk,
        document_id=chunk.document_id,
        document_title=chunk.document.title,
        ordinal=chunk.ordinal,
        heading=chunk.heading,
        text=chunk.text,
        vector_score=round(vector_score, 6),
        keyword_score=round(keyword_score, 6),
        combined_score=round(combined_score, 6),
    )


def search(
    question: str,
    audience: scope.Audience,
    *,
    profile: str = UsageEvent.Profile.CONSOLE,
    actor=None,
    on_date=None,
    use_cache: bool = True,
) -> RetrievalResult:
    """
    Rank the visible passages for this audience. An audience with nothing
    visible is refused without embedding the question, so a reader who may see
    nothing costs nothing.
    """
    query = (question or "").strip()[:MAX_QUESTION]
    if not query:
        return RetrievalResult(refused=True, reason="The question was empty.")

    providers = selectors.providers_in_order()
    version = versions.current()
    model_signature = _model_signature(providers)
    cache_key = f"assistant:retrieval:{_hash(audience.signature, query, model_signature, version)}"

    if use_cache:
        cached = cache.get(cache_key)
        if cached and cached.get("refused") and not cached.get("rows"):
            return RetrievalResult(
                top_vector=cached.get("top_vector", 0.0),
                top_keyword=cached.get("top_keyword", 0.0),
                refused=True,
                reason=cached.get("reason", ""),
                from_cache=True,
            )
        if cached:
            hydrated = visible_chunk_map(
                audience, [row["chunk_id"] for row in cached["rows"]], on_date=on_date, model=cached["model"]
            )
            sources = []
            for row in cached["rows"]:
                chunk = hydrated.get(row["chunk_id"])
                if chunk is None:
                    continue
                sources.append(
                    _source_from(
                        chunk,
                        vector_score=row["vector"],
                        keyword_score=row["keyword"],
                        combined_score=row["combined"],
                    )
                )
            if sources:
                return RetrievalResult(
                    sources=sources[:TOP_K],
                    top_vector=cached.get("top_vector", 0.0),
                    top_keyword=cached.get("top_keyword", 0.0),
                    model=cached.get("model", ""),
                    dimension=int(cached.get("dimension", 0)),
                    refused=cached.get("refused", False),
                    reason=cached.get("reason", ""),
                    from_cache=True,
                )

    rows = _visible_chunk_rows(audience, on_date)
    if not rows:
        result = RetrievalResult(refused=True, reason="No approved knowledge is visible to this audience yet.")
        if use_cache:
            cache.set(
                cache_key,
                {
                    "rows": [],
                    "model": "",
                    "dimension": 0,
                    "top_vector": 0.0,
                    "top_keyword": 0.0,
                    "refused": True,
                    "reason": result.reason,
                },
                RETRIEVAL_TTL,
            )
        return result

    vector, model, dimension = _question_embedding(query, providers, profile=profile, actor=actor)

    chunk_models = {(row.embedding_model, row.dimension) for row in rows}
    if (model, dimension) not in chunk_models:
        # The embedding model changed, or the cached vector is for another
        # model. Embed again with the model now in service.
        cache.delete(f"assistant:qembed:{_hash(model_signature, query)}")
        vector, model, dimension = _question_embedding(query, providers, profile=profile, actor=actor)

    eligible = [row for row in rows if row.embedding_model == model and row.dimension == len(vector)]
    if not eligible:
        return RetrievalResult(
            refused=True,
            reason=(
                "The stored passages were embedded by a different model. "
                "Read the document again before it can answer."
            ),
            model=model,
            dimension=dimension,
        )

    query_tokens = tokenise(query)
    scored = []
    for chunk in eligible:
        scored.append(
            (
                chunk,
                cosine(vector, chunk.embedding),
                keyword_score(query_tokens, chunk.text),
            )
        )

    top_vector = max((row[1] for row in scored), default=0.0)
    top_keyword = max((row[2] for row in scored), default=0.0)

    vector_ranks = [
        chunk.pk
        for chunk, _, _ in sorted(scored, key=lambda row: (-row[1], row[0].pk))
    ]
    keyword_ranks = [
        chunk.pk
        for chunk, _, keyword in sorted(scored, key=lambda row: (-row[2], -row[1], row[0].pk))
        if keyword > 0
    ]
    combined = merge_rankings(vector_ranks, keyword_ranks)

    ordered = sorted(
        scored,
        key=lambda row: (-combined.get(row[0].pk, 0.0), -row[1], row[0].pk),
    )
    sources = [
        _source_from(
            chunk,
            vector_score=vector_s,
            keyword_score=keyword_s,
            combined_score=combined.get(chunk.pk, 0.0),
        )
        for chunk, vector_s, keyword_s in ordered
    ]

    refused = top_vector < MIN_SCORE and top_keyword <= 0
    reason = ""
    if refused:
        reason = "Nothing in the approved knowledge matched this question closely enough."
        # A refusal must not carry passages: the caller may not use them.
        sources = []

    ranked_rows = [
        {
            "chunk_id": source.chunk_id,
            "vector": source.vector_score,
            "keyword": source.keyword_score,
            "combined": source.combined_score,
        }
        for source in sources[:TOP_K]
    ]
    if use_cache:
        cache.set(
            cache_key,
            {
                "rows": ranked_rows,
                "model": model,
                "dimension": dimension,
                "top_vector": round(top_vector, 6),
                "top_keyword": round(top_keyword, 6),
                "refused": refused,
                "reason": reason,
            },
            RETRIEVAL_TTL,
        )

    return RetrievalResult(
        sources=sources[:TOP_K],
        top_vector=round(top_vector, 6),
        top_keyword=round(top_keyword, 6),
        model=model,
        dimension=dimension,
        refused=refused,
        reason=reason,
    )


def score_document(document: KnowledgeDocument, question: str, *, actor=None) -> list[Source]:
    """
    The per-document check: score this document's own passages only.

    The question is embedded (the one provider call); no answer is generated.
    """
    query = (question or "").strip()[:MAX_QUESTION]
    if not query:
        return []
    chunks = [
        chunk
        for chunk in document.chunks.select_related("document").order_by("ordinal")
        if chunk.embedding
    ]
    if not chunks:
        return []

    providers = selectors.providers_in_order()
    vector, model, dimension = _question_embedding(
        query, providers, profile=UsageEvent.Profile.CONSOLE, actor=actor
    )
    eligible = [chunk for chunk in chunks if chunk.embedding_model == model and chunk.dimension == len(vector)]
    if not eligible:
        return []

    query_tokens = tokenise(query)
    scored = [
        (chunk, cosine(vector, chunk.embedding), keyword_score(query_tokens, chunk.text))
        for chunk in eligible
    ]
    vector_ranks = [
        chunk.pk for chunk, _, _ in sorted(scored, key=lambda row: (-row[1], row[0].pk))
    ]
    keyword_ranks = [
        chunk.pk
        for chunk, _, keyword in sorted(scored, key=lambda row: (-row[2], -row[1], row[0].pk))
        if keyword > 0
    ]
    combined = merge_rankings(vector_ranks, keyword_ranks)
    ordered = sorted(scored, key=lambda row: (-combined.get(row[0].pk, 0.0), -row[1], row[0].pk))
    return [
        _source_from(
            chunk,
            vector_score=vector_s,
            keyword_score=keyword_s,
            combined_score=combined.get(chunk.pk, 0.0),
        )
        for chunk, vector_s, keyword_s in ordered
    ]
