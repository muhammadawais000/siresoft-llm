"""Chunking tests. All pure logic -- no DB, no ML models -- so these run
fast and need nothing beyond the Django settings.
"""

from django.test import SimpleTestCase, override_settings
from langchain_core.documents import Document as LCDocument

from documents.models import Document
from rag.splitting import split_document, token_len


class TokenLenTests(SimpleTestCase):
    def test_counts_tokens_not_characters(self):
        text = "The quick brown fox jumps over the lazy dog."
        count = token_len(text)
        self.assertGreater(count, 0)
        self.assertLess(count, len(text))

    def test_empty_string_is_zero_tokens(self):
        self.assertEqual(token_len(""), 0)


class SplitGenericDocumentTests(SimpleTestCase):
    def _document(self, source_type=Document.SourceType.TEXT):
        # split_document only reads .source_type -- an unsaved instance is
        # fine and keeps this test DB-free.
        return Document(title="Test", source_type=source_type)

    @override_settings(CHUNK_SIZE=800, CHUNK_OVERLAP=120)
    def test_short_text_becomes_a_single_chunk(self):
        lc_docs = [LCDocument(page_content="A short paragraph about vacation policy.", metadata={"page_number": None})]
        chunks = split_document(self._document(), lc_docs)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_index"], 0)
        self.assertIn("vacation", chunks[0]["content"])
        self.assertIsNone(chunks[0]["page_number"])

    @override_settings(CHUNK_SIZE=30, CHUNK_OVERLAP=5)
    def test_long_text_is_split_into_sequential_chunks(self):
        long_text = " ".join(f"This is sentence number {i} with a few extra words." for i in range(40))
        lc_docs = [LCDocument(page_content=long_text, metadata={"page_number": None})]
        chunks = split_document(self._document(), lc_docs)

        self.assertGreater(len(chunks), 1)
        self.assertEqual([c["chunk_index"] for c in chunks], list(range(len(chunks))))
        # RecursiveCharacterTextSplitter tries to respect chunk_size but can
        # exceed it slightly at a boundary -- assert "roughly respected",
        # not an exact cap, since the exact splitting behavior belongs to
        # LangChain, not this project.
        for c in chunks:
            self.assertLess(c["token_count"], 30 * 2)

    def test_page_number_metadata_is_preserved_per_source_document(self):
        lc_docs = [
            LCDocument(page_content="Page one content here.", metadata={"page_number": 1}),
            LCDocument(page_content="Page two content here.", metadata={"page_number": 2}),
        ]
        chunks = split_document(self._document(source_type=Document.SourceType.PDF), lc_docs)
        self.assertEqual({c["page_number"] for c in chunks}, {1, 2})

    def test_blank_source_content_produces_no_chunks(self):
        lc_docs = [LCDocument(page_content="   \n\n   ", metadata={"page_number": None})]
        chunks = split_document(self._document(), lc_docs)
        self.assertEqual(chunks, [])

    def test_chunk_index_restarts_at_zero_per_call(self):
        # Guards against chunk_index leaking state across documents -- each
        # split_document() call must number its own chunks from 0.
        lc_docs = [LCDocument(page_content="Some content.", metadata={"page_number": None})]
        first = split_document(self._document(), lc_docs)
        second = split_document(self._document(), lc_docs)
        self.assertEqual(first[0]["chunk_index"], 0)
        self.assertEqual(second[0]["chunk_index"], 0)


class SplitMarkdownDocumentTests(SimpleTestCase):
    def _document(self):
        return Document(title="Handbook", source_type=Document.SourceType.MARKDOWN)

    @override_settings(CHUNK_SIZE=800, CHUNK_OVERLAP=120)
    def test_headers_become_section_headings(self):
        markdown = (
            "# Company Handbook\n\n"
            "## Vacation Policy\n\n"
            "Employees accrue 15 days per year.\n\n"
            "## Expense Reporting\n\n"
            "Submit within 30 days.\n"
        )
        chunks = split_document(self._document(), [LCDocument(page_content=markdown, metadata={})])
        headings = [c["section_heading"] for c in chunks]
        self.assertTrue(any("Vacation Policy" in h for h in headings))
        self.assertTrue(any("Expense Reporting" in h for h in headings))

    @override_settings(CHUNK_SIZE=800, CHUNK_OVERLAP=120)
    def test_nested_headers_are_joined_with_arrow(self):
        markdown = "# Handbook\n\n## Expenses\n\n### Deadlines\n\nSubmit within 30 days.\n"
        chunks = split_document(self._document(), [LCDocument(page_content=markdown, metadata={})])
        self.assertIn("Handbook > Expenses > Deadlines", chunks[0]["section_heading"])

    def test_markdown_without_headers_falls_back_to_whole_document(self):
        chunks = split_document(
            self._document(), [LCDocument(page_content="Just a paragraph, no headers at all.", metadata={})]
        )
        self.assertEqual(len(chunks), 1)
        self.assertIn("Just a paragraph", chunks[0]["content"])
