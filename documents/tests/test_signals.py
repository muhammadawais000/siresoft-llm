"""Deleting a Document must also clean up its vectors in Qdrant, however
the delete happens (view, admin, management command, cascade) -- covered
by a pre_delete signal (documents/signals.py) rather than a call each
individual call site has to remember to make.
"""

import uuid
from unittest.mock import patch

from django.test import TestCase

from documents.models import Document


def make_document():
    return Document.objects.create(
        title="Doc",
        source_type=Document.SourceType.TEXT,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=Document.Status.INDEXED,
    )


class DeleteDocumentVectorsSignalTests(TestCase):
    @patch("documents.signals.delete_by_document")
    def test_deleting_a_document_clears_its_qdrant_vectors(self, mock_delete):
        doc = make_document()
        doc_id = doc.id

        doc.delete()

        mock_delete.assert_called_once_with(doc_id)
