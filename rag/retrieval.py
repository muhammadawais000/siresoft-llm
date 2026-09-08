"""Hybrid retrieval: dense (pgvector cosine) + sparse (Postgres full-text),
fused with Reciprocal Rank Fusion, then re-ranked with a cross-encoder.

This is the whole point of "genuinely good" retrieval per the project
brief: dense search alone misses exact product names/error codes/numbers
that a keyword search catches (see `rag/management/commands/prove_retrieval.py`
for a concrete before/after), and RRF fusion + reranking is what turns two
mediocre-on-their-own result sets into one good one.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank

from rag.embeddings import embed_query
from rag.models import Chunk
from rag.reranker import rerank as cross_encoder_rerank

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float  # final, post-rerank relevance score in [0, 1]
    dense_rank: int | None = None
    sparse_rank: int | None = None

    def to_citation(self, rank: int) -> dict:
        # `rank` is the citation's position (1-indexed), matching the [N]
        # marker the LLM was instructed to cite in rag.generation.format_context
        # -- it's the caller's to assign since it depends on this chunk's
        # position within the final results list, not anything intrinsic
        # to the chunk itself.
        return {
            "rank": rank,
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "document_title": self.chunk.document.title,
            "page_number": self.chunk.page_number,
            "section_heading": self.chunk.section_heading,
            "content": self.chunk.content,
            "score": round(self.score, 4),
        }


def get_all_chunks(document_ids: list[int] | None = None) -> list[RetrievedChunk]:
    """Every chunk of the document(s) in scope, in document/reading order --
    not a relevance-ranked subset. Used for whole-document requests
    ("summarize this file") where hybrid top-k semantic search is the
    wrong tool: a summary needs everything, not just what best matches
    a query embedding.

    No score is meaningful here (nothing was ranked), so every entry gets
    score=1.0 -- high enough that it's never mistaken for a low-confidence
    match if it flows into the same citation/threshold code paths as
    retrieve()'s results.
    """
    qs = Chunk.objects.select_related("document").order_by("document_id", "chunk_index")
    if document_ids:
        qs = qs.filter(document_id__in=document_ids)
    return [RetrievedChunk(chunk=c, score=1.0) for c in qs]


def dense_search(query_vector: list[float], pool_size: int, document_ids=None) -> list[int]:
    from pgvector.django import CosineDistance

    qs = Chunk.objects.all()
    if document_ids:
        qs = qs.filter(document_id__in=document_ids)
    qs = qs.annotate(distance=CosineDistance("embedding", query_vector)).order_by("distance")
    return list(qs.values_list("id", flat=True)[:pool_size])


def sparse_search(query_text: str, pool_size: int, document_ids=None) -> list[int]:
    # websearch_to_tsquery understands natural query syntax (quotes,
    # -exclusions) instead of requiring Postgres's terser tsquery operators.
    search_query = SearchQuery(query_text, search_type="websearch")
    qs = Chunk.objects.filter(content_tsv=search_query)
    if document_ids:
        qs = qs.filter(document_id__in=document_ids)
    qs = qs.annotate(rank=SearchRank("content_tsv", search_query)).order_by("-rank")
    return list(qs.values_list("id", flat=True)[:pool_size])


def reciprocal_rank_fusion(*ranked_id_lists: list[int], k: int) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ids in ranked_id_lists:
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    candidate_pool: int | None = None,
    similarity_threshold: float | None = None,
    document_ids: list[int] | None = None,
) -> list[RetrievedChunk]:
    """Run the full hybrid retrieval pipeline for `query`.

    Returns an empty list if nothing clears `similarity_threshold` — the
    caller (the generation chain, in Phase 4) is expected to treat that as
    "no relevant content found" and short-circuit before calling the LLM,
    rather than generating from empty/irrelevant context.
    """
    top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    candidate_pool = candidate_pool if candidate_pool is not None else settings.RETRIEVAL_CANDIDATE_POOL
    similarity_threshold = (
        similarity_threshold if similarity_threshold is not None else settings.RETRIEVAL_SIMILARITY_THRESHOLD
    )

    query_vector = embed_query(query)
    dense_ids = dense_search(query_vector, candidate_pool, document_ids)
    sparse_ids = sparse_search(query, candidate_pool, document_ids)

    if not dense_ids and not sparse_ids:
        return []

    fused_scores = reciprocal_rank_fusion(dense_ids, sparse_ids, k=settings.RETRIEVAL_RRF_K)
    fused_ranked_ids = sorted(fused_scores, key=fused_scores.get, reverse=True)[:candidate_pool]

    dense_rank_by_id = {cid: rank for rank, cid in enumerate(dense_ids, start=1)}
    sparse_rank_by_id = {cid: rank for rank, cid in enumerate(sparse_ids, start=1)}

    chunks_by_id = {
        c.id: c for c in Chunk.objects.filter(id__in=fused_ranked_ids).select_related("document")
    }
    ordered_chunks = [chunks_by_id[cid] for cid in fused_ranked_ids if cid in chunks_by_id]
    if not ordered_chunks:
        return []

    rerank_scores = cross_encoder_rerank(query, [c.content for c in ordered_chunks])

    results = [
        RetrievedChunk(
            chunk=chunk,
            score=score,
            dense_rank=dense_rank_by_id.get(chunk.id),
            sparse_rank=sparse_rank_by_id.get(chunk.id),
        )
        for chunk, score in zip(ordered_chunks, rerank_scores)
    ]
    results.sort(key=lambda r: r.score, reverse=True)
    results = results[:top_k]

    above_threshold = [r for r in results if r.score >= similarity_threshold]
    if not above_threshold:
        logger.info(
            "No chunks cleared similarity_threshold=%.3f for query=%r (best score=%.3f)",
            similarity_threshold,
            query,
            results[0].score if results else -1.0,
        )
    return above_threshold
