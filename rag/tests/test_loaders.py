"""Loader edge cases that don't need real ML models or system OCR
binaries: encoding fallback and empty-content detection. PDF/DOCX/URL
loaders are exercised in Phase 2's manual ingestion testing and aren't
re-verified here since they'd otherwise pull in pymupdf/docx2txt/network
fixtures for marginal extra coverage.
"""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from core.exceptions import EmptyDocumentError
from rag.loaders import _load_text


class LoadTextTests(SimpleTestCase):
    def _write(self, data: bytes) -> str:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
            f.write(data)
            return f.name

    def test_utf8_file_loads_correctly(self):
        path = self._write("Hello world, café.".encode("utf-8"))
        docs = _load_text(path)
        self.assertEqual(len(docs), 1)
        self.assertIn("café", docs[0].page_content)
        Path(path).unlink()

    def test_non_utf8_file_does_not_crash(self):
        # A latin-1 encoded byte sequence that isn't valid UTF-8 -- the
        # loader must fall back to a lossy decode instead of raising.
        path = self._write("café report".encode("latin-1"))
        docs = _load_text(path)  # must not raise
        self.assertEqual(len(docs), 1)
        self.assertGreater(len(docs[0].page_content), 0)
        Path(path).unlink()

    def test_empty_file_raises_empty_document_error(self):
        path = self._write(b"")
        with self.assertRaises(EmptyDocumentError):
            _load_text(path)
        Path(path).unlink()

    def test_whitespace_only_file_raises_empty_document_error(self):
        path = self._write(b"   \n\n\t  ")
        with self.assertRaises(EmptyDocumentError):
            _load_text(path)
        Path(path).unlink()
