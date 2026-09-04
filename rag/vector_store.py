"""Dedicated vector store access (Qdrant).

Dense/cosine search used to run as a pgvector query against the same
Postgres table as everything else; it now lives in its own Qdrant
collection instead, addressed only through this module. PostgreSQL keeps
the relational data and the full-text (tsvector) index -- Qdrant's only
job is "given a query vector, return the nearest chunk ids".

Point IDs in the collection are the Chunk primary keys directly, so a
chunk's vector can always be found/removed by the same id used everywhere
else in this codebase -- no separate id-mapping table needed. Every point
also carries `document_id` in its payload, which is what makes
per-document filtering (a "summarize this document" scoped query, or
cleaning up one document's vectors) a server-side filter instead of a
fetch-then-discard.
"""

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

_collection_ensured = False


@lru_cache(maxsize=1)
def get_client():
    from qdrant_client import QdrantClient

    return QdrantClient(url=settings.QDRANT_URL)


def ensure_collection() -> None:
    """Create the collection if it doesn't exist yet. Checked once per
    process (module-level flag) rather than on every call -- collection
    existence doesn't change mid-process, so there's no reason to pay a
    network round trip per upsert/search once it's confirmed present.
    """
    global _collection_ensured
    if _collection_ensured:
        return

    from qdrant_client.models import Distance, VectorParams

    client = get_client()
    if not client.collection_exists(settings.QDRANT_COLLECTION):
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
        )
        logger.info(
            "Created Qdrant collection %s (dim=%d)", settings.QDRANT_COLLECTION, settings.EMBEDDING_DIMENSIONS
        )
    _collection_ensured = True


def upsert_chunks(document_id: int, chunk_ids: list[int], vectors: list[list[float]]) -> None:
    """Write (or overwrite) the vectors for a batch of already-persisted
    chunks. Called right after `rag.indexing.persist_chunks` bulk-creates
    the Chunk rows, using the primary keys Postgres just assigned them.
    """
    if not chunk_ids:
        return

    from qdrant_client.models import PointStruct

    ensure_collection()
    points = [
        PointStruct(id=chunk_id, vector=vector, payload={"document_id": document_id})
        for chunk_id, vector in zip(chunk_ids, vectors)
    ]
    get_client().upsert(collection_name=settings.QDRANT_COLLECTION, points=points)


def delete_by_document(document_id: int) -> None:
    """Remove every point belonging to a document. Called before a
    re-index rewrites a document's chunks, and when the document itself is
    deleted -- so a stale vector can never outlive the Chunk row it came
    from, on either path.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    ensure_collection()
    get_client().delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
    )


def search_with_scores(
    query_vector: list[float], limit: int, document_ids: list[int] | None = None
) -> list[tuple[int, float]]:
    """Nearest-neighbour (chunk id, cosine similarity) pairs, best match
    first. `search()` below is the id-only convenience most callers want --
    RRF fusion only ever uses rank position, not the raw score -- this is
    for the rarer caller (the prove_retrieval diagnostic) that wants the
    actual similarity value too.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    ensure_collection()
    query_filter = None
    if document_ids:
        query_filter = Filter(must=[FieldCondition(key="document_id", match=MatchAny(any=document_ids))])

    response = get_client().query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_vector,
        limit=limit,
        query_filter=query_filter,
    )
    return [(point.id, point.score) for point in response.points]


def search(query_vector: list[float], limit: int, document_ids: list[int] | None = None) -> list[int]:
    """Nearest-neighbour chunk ids for `query_vector`, best match first."""
    return [chunk_id for chunk_id, _score in search_with_scores(query_vector, limit, document_ids)]
