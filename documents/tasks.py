"""Background ingestion pipeline: parsing -> chunking -> embedding -> indexed.

Runs entirely inside `ingest_document`, which is the only thing the upload
views ever call `.delay()` on — the HTTP request returns as soon as the
Document row + task are created, per the "never block on a 200-page PDF"
requirement.
"""

import logging

from celery import shared_task
from django.db import transaction

from core.exceptions import SiresoftRAGError
from documents.models import Document
from rag.indexing import embed_chunks, persist_chunks
from rag.loaders import load_document
from rag.splitting import split_document

logger = logging.getLogger(__name__)


def _set_status(document: Document, status: str) -> None:
    document.status = status
    document.save(update_fields=["status", "updated_at"])


@shared_task(bind=True, soft_time_limit=900, time_limit=1200)
def ingest_document(self, document_id: int) -> None:
    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        logger.warning("ingest_document called for missing document id=%s", document_id)
        return

    logger.info("Starting ingestion for document %s (%s)", document.id, document.title)

    try:
        _set_status(document, Document.Status.PARSING)
        lc_docs = load_document(document)

        _set_status(document, Document.Status.CHUNKING)
        chunk_dicts = split_document(document, lc_docs)
        if not chunk_dicts:
            raise SiresoftRAGError("Document produced no usable chunks after splitting.")

        _set_status(document, Document.Status.EMBEDDING)
        vectors = embed_chunks(chunk_dicts)

        with transaction.atomic():
            persist_chunks(document, chunk_dicts, vectors)
            document.status = Document.Status.INDEXED
            document.error_message = ""
            document.save()

        logger.info(
            "Finished ingestion for document %s: %d chunks", document.id, document.chunk_count
        )

    except Exception as exc:
        logger.exception("Ingestion failed for document %s", document.id)
        document.status = Document.Status.FAILED
        document.error_message = _human_readable_error(exc)
        document.save(update_fields=["status", "error_message", "updated_at"])


def _human_readable_error(exc: Exception) -> str:
    """Map an exception to a short message safe to show directly in the UI.

    Our own IngestionError/EmptyDocumentError/etc. already carry a
    user-facing message. Anything else (a library internals leak) is
    trimmed rather than shown as a raw traceback.
    """
    if isinstance(exc, SiresoftRAGError):
        return str(exc)
    message = str(exc) or exc.__class__.__name__
    return message[:500]
