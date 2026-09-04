"""Persist split, embedded chunks: content + metadata to Postgres, vectors
to Qdrant."""

import logging

from django.conf import settings
from django.utils import timezone

from documents.models import Document
from rag.embeddings import embed_documents
from rag.models import Chunk
from rag.vector_store import delete_by_document, upsert_chunks

logger = logging.getLogger(__name__)


def embed_chunks(chunk_dicts: list[dict]) -> list[list[float]]:
    """CPU-bound embedding step, deliberately kept outside any DB
    transaction so a slow batch doesn't hold a connection/locks open.
    """
    return embed_documents([c["content"] for c in chunk_dicts])


def persist_chunks(document: Document, chunk_dicts: list[dict], vectors: list[list[float]]) -> int:
    """Bulk-write already-embedded chunks for a document, replacing any that
    already exist (the re-index path). Returns the number of chunks written.
    """
    # Chunks are immutable and re-created wholesale on re-index rather than
    # diffed/updated in place — this keeps chunk_index/content always
    # mutually consistent, and re-indexing is expected to be rare enough
    # that the extra writes don't matter. The Qdrant side is cleared the
    # same way, so a re-index can never leave an orphaned vector behind
    # from the chunk it's replacing.
    Chunk.objects.filter(document=document).delete()
    delete_by_document(document.id)

    objs = [
        Chunk(
            document=document,
            content=c["content"],
            chunk_index=c["chunk_index"],
            page_number=c["page_number"],
            section_heading=c["section_heading"],
            token_count=c["token_count"],
        )
        for c in chunk_dicts
    ]
    Chunk.objects.bulk_create(objs, batch_size=200)
    # bulk_create returns the objects with their new primary keys populated
    # (Postgres supports RETURNING), so this is the first point the chunk
    # ids exist to pair up with their vectors.
    upsert_chunks(document.id, [obj.id for obj in objs], vectors)

    embedding_model_name = settings.EMBEDDING_PROFILES[settings.EMBEDDING_PROFILE]["model_name"]
    page_numbers = [c["page_number"] for c in chunk_dicts if c["page_number"] is not None]

    document.chunk_count = len(objs)
    document.embedding_model_name = embedding_model_name
    document.page_count = max(page_numbers) if page_numbers else None
    document.indexed_at = timezone.now()

    logger.info(
        "Indexed document %s: %d chunks, model=%s", document.id, len(objs), embedding_model_name
    )
    return len(objs)
