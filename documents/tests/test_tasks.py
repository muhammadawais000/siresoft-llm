"""Ingestion pipeline orchestration tests.

load_document() and embed_chunks() (the loader-parsing and ML-embedding
boundaries) are mocked so these run fast and offline -- what's under test
is ingest_document()'s own responsibility: status transitions, error
handling that never leaks a raw traceback into Document.error_message,
and correct chunk replacement on re-index.
"""

import uuid
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from langchain_core.documents import Document as LCDocument

from core.exceptions import EmptyDocumentError
from documents.models import Document
from documents.tasks import ingest_document
from rag.models import Chunk


def make_document(**kwargs):
    defaults = dict(
        title="Test Doc",
        source_type=Document.SourceType.TEXT,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=Document.Status.QUEUED,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


def fake_embed_chunks(chunk_dicts):
    return [[0.0] * settings.EMBEDDING_DIMENSIONS for _ in chunk_dicts]


class IngestDocumentTaskTests(TestCase):
    @patch("documents.tasks.embed_chunks", side_effect=fake_embed_chunks)
    @patch("documents.tasks.load_document")
    def test_successful_ingestion_marks_document_indexed(self, mock_load, mock_embed):
        doc = make_document()
        mock_load.return_value = [LCDocument(page_content="Some real content to chunk.", metadata={"page_number": None})]

        ingest_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.Status.INDEXED)
        self.assertEqual(doc.error_message, "")
        self.assertGreater(doc.chunk_count, 0)
        self.assertIsNotNone(doc.indexed_at)
        expected_model = settings.EMBEDDING_PROFILES[settings.EMBEDDING_PROFILE]["model_name"]
        self.assertEqual(doc.embedding_model_name, expected_model)

    @patch("documents.tasks.load_document")
    def test_loader_failure_marks_document_failed_with_its_message(self, mock_load):
        doc = make_document()
        mock_load.side_effect = EmptyDocumentError("File is empty.")

        ingest_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.Status.FAILED)
        self.assertIn("empty", doc.error_message.lower())

    @patch("documents.tasks.load_document")
    def test_generic_exception_is_truncated_not_leaked_verbatim(self, mock_load):
        doc = make_document()
        mock_load.side_effect = RuntimeError("x" * 600)

        ingest_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.Status.FAILED)
        self.assertLessEqual(len(doc.error_message), 500)

    def test_missing_document_id_does_not_raise(self):
        # e.g. the document was deleted between being queued and the task
        # actually running -- this must log and return, not crash the worker.
        ingest_document(999999)

    @patch("documents.tasks.embed_chunks", side_effect=fake_embed_chunks)
    @patch("documents.tasks.load_document")
    def test_reindex_replaces_existing_chunks_rather_than_appending(self, mock_load, mock_embed):
        doc = make_document(status=Document.Status.INDEXED)
        Chunk.objects.create(
            document=doc, content="stale chunk from a previous version", chunk_index=0,
            embedding=[0.0] * settings.EMBEDDING_DIMENSIONS,
        )
        mock_load.return_value = [LCDocument(page_content="Brand new content entirely.", metadata={"page_number": None})]

        ingest_document(doc.id)

        chunks = list(doc.chunks.all())
        self.assertEqual(len(chunks), 1)
        self.assertIn("Brand new content", chunks[0].content)

    @patch("documents.tasks.embed_chunks", side_effect=fake_embed_chunks)
    @patch("documents.tasks.load_document")
    def test_no_usable_chunks_after_splitting_marks_document_failed(self, mock_load, mock_embed):
        doc = make_document()
        # Whitespace-only content survives the loader but splitting drops it.
        mock_load.return_value = [LCDocument(page_content="   ", metadata={"page_number": None})]

        ingest_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.Status.FAILED)
        mock_embed.assert_not_called()
