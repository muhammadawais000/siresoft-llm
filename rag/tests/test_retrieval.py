"""Retrieval scoring tests.

RRF fusion is pure logic (SimpleTestCase, no DB). sparse_search exercises
real Postgres full-text search against a live test database. dense_search
is a thin pass-through to rag.vector_store.search (Qdrant) -- covered by
DenseSearchTests mocking that module boundary, the same way retrieve()
mocks embed_query / the cross-encoder, so tests stay fast, offline, and
don't require a running Qdrant instance. What's under test there is this
codebase's own orchestration (fusion, candidate pooling, threshold
filtering), not Qdrant's or the models' own correctness.
"""

import uuid
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from documents.models import Document
from rag.models import Chunk
from rag.retrieval import (
    RetrievedChunk,
    dense_search,
    get_all_chunks,
    reciprocal_rank_fusion,
    retrieve,
    sparse_search,
)

DIM = settings.EMBEDDING_DIMENSIONS


def unit_vector(active_index):
    v = [0.0] * DIM
    v[active_index % DIM] = 1.0
    return v


def make_document(title="Doc"):
    return Document.objects.create(
        title=title,
        source_type=Document.SourceType.TEXT,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=Document.Status.INDEXED,
    )


def make_chunk(document, content, embedding=None, chunk_index=0, page_number=None):
    # `embedding` is accepted (and ignored) for call-site compatibility --
    # vectors live in Qdrant now, not on the Chunk row -- so existing
    # callers that pass a hand-crafted vector don't all need editing.
    return Chunk.objects.create(
        document=document,
        content=content,
        chunk_index=chunk_index,
        page_number=page_number,
    )


class ReciprocalRankFusionTests(SimpleTestCase):
    def test_item_ranked_first_in_both_lists_wins(self):
        scores = reciprocal_rank_fusion([1, 2, 3], [1, 3, 2], k=60)
        self.assertEqual(max(scores, key=scores.get), 1)

    def test_item_only_in_one_list_still_scores(self):
        scores = reciprocal_rank_fusion([5], [], k=60)
        self.assertIn(5, scores)
        self.assertAlmostEqual(scores[5], 1 / 61)

    def test_earlier_rank_position_scores_higher(self):
        scores = reciprocal_rank_fusion([10, 20, 30], k=60)
        self.assertGreater(scores[10], scores[20])
        self.assertGreater(scores[20], scores[30])

    def test_appearing_in_both_lists_outscores_appearing_in_one(self):
        both = reciprocal_rank_fusion([1, 2], [2, 1], k=60)
        only_one = reciprocal_rank_fusion([2], [], k=60)
        self.assertGreater(both[2], only_one[2])


class DenseSearchTests(SimpleTestCase):
    """dense_search is a thin pass-through to rag.vector_store.search
    (Qdrant) -- mocked at that boundary rather than requiring a live
    Qdrant instance for what's really just an argument-forwarding check.
    """

    @patch("rag.retrieval.vector_search")
    def test_forwards_query_vector_pool_size_and_document_ids_and_returns_result(self, mock_search):
        mock_search.return_value = [3, 1, 2]

        results = dense_search([0.1, 0.2], pool_size=10, document_ids=[7])

        mock_search.assert_called_once_with([0.1, 0.2], limit=10, document_ids=[7])
        self.assertEqual(results, [3, 1, 2])


class SparseSearchTests(TestCase):
    def setUp(self):
        self.doc = make_document()
        self.match = make_chunk(self.doc, "Error code E-4471 indicates a calibration fault.", unit_vector(0))
        self.other = make_chunk(self.doc, "Unrelated paragraph about vacation days.", unit_vector(1), chunk_index=1)

    def test_keyword_match_is_found(self):
        results = sparse_search("E-4471", pool_size=10)
        self.assertIn(self.match.id, results)

    def test_no_match_returns_empty(self):
        results = sparse_search("nonexistent gibberish zzqx", pool_size=10)
        self.assertEqual(results, [])

    def test_document_ids_filter_applies_to_full_text_search_too(self):
        other_doc = make_document("Other")
        make_chunk(other_doc, "Error code E-4471 mentioned here too.", unit_vector(0))
        results = sparse_search("E-4471", pool_size=10, document_ids=[self.doc.id])
        self.assertEqual(results, [self.match.id])


class RetrieveOrchestrationTests(TestCase):
    """Exercises retrieve()'s own logic (fusion -> rerank -> threshold),
    with embed_query, the cross-encoder, and dense_search (Qdrant) all
    mocked at their module boundaries -- sparse_search is the one real
    call, against a live test-database full-text index, since that part
    doesn't need an external service.
    """

    def setUp(self):
        self.doc = make_document()
        self.relevant = make_chunk(self.doc, "Error E-4471 calibration fault", unit_vector(0), chunk_index=0)
        self.irrelevant = make_chunk(self.doc, "Completely unrelated text", unit_vector(5), chunk_index=1)

    @patch("rag.retrieval.dense_search")
    @patch("rag.retrieval.cross_encoder_rerank")
    @patch("rag.retrieval.embed_query")
    def test_relevant_chunk_returned_above_threshold(self, mock_embed_query, mock_rerank, mock_dense_search):
        mock_embed_query.return_value = unit_vector(0)
        mock_dense_search.return_value = [self.relevant.id, self.irrelevant.id]
        mock_rerank.side_effect = lambda query, passages: [0.9 for _ in passages]

        results = retrieve("what does E-4471 mean", top_k=5, similarity_threshold=0.01)

        self.assertTrue(any(r.chunk.id == self.relevant.id for r in results))

    @patch("rag.retrieval.dense_search")
    @patch("rag.retrieval.cross_encoder_rerank")
    @patch("rag.retrieval.embed_query")
    def test_nothing_returned_when_all_scores_are_near_zero(self, mock_embed_query, mock_rerank, mock_dense_search):
        # Empirically (see manage.py prove_retrieval, and rag.reranker's
        # docstring), the reranker's calibrated Sigmoid probability sits
        # well under 0.01 for genuinely irrelevant content and 0.7+ for
        # confident real matches -- a wide, well-separated range once
        # CrossEncoder's already-applied Sigmoid isn't double-applied.
        mock_embed_query.return_value = unit_vector(0)
        mock_dense_search.return_value = [self.relevant.id, self.irrelevant.id]
        mock_rerank.side_effect = lambda query, passages: [0.0005 for _ in passages]

        results = retrieve("irrelevant question", top_k=5, similarity_threshold=0.01)

        self.assertEqual(results, [])

    @patch("rag.retrieval.dense_search")
    @patch("rag.retrieval.cross_encoder_rerank")
    @patch("rag.retrieval.embed_query")
    def test_top_k_limits_result_count(self, mock_embed_query, mock_rerank, mock_dense_search):
        for i in range(2, 8):
            make_chunk(self.doc, f"content {i}", unit_vector(i), chunk_index=i)
        mock_embed_query.return_value = unit_vector(0)
        mock_dense_search.return_value = list(Chunk.objects.values_list("id", flat=True))
        mock_rerank.side_effect = lambda query, passages: [0.9 for _ in passages]

        results = retrieve("query", top_k=3, similarity_threshold=0.0)

        self.assertLessEqual(len(results), 3)

    @patch("rag.retrieval.dense_search")
    @patch("rag.retrieval.cross_encoder_rerank")
    @patch("rag.retrieval.embed_query")
    def test_no_chunks_in_db_returns_empty_without_calling_rerank(self, mock_embed_query, mock_rerank, mock_dense_search):
        Chunk.objects.all().delete()
        mock_embed_query.return_value = unit_vector(0)
        mock_dense_search.return_value = []

        results = retrieve("anything", top_k=5)

        self.assertEqual(results, [])
        mock_rerank.assert_not_called()

    def test_to_citation_includes_the_assigned_rank(self):
        result = RetrievedChunk(chunk=self.relevant, score=0.812)
        citation = result.to_citation(rank=2)
        self.assertEqual(citation["rank"], 2)
        self.assertEqual(citation["chunk_id"], self.relevant.id)
        self.assertEqual(citation["score"], 0.812)


class GetAllChunksTests(TestCase):
    """get_all_chunks() backs whole-document requests ("summarize this") --
    it must return every chunk, unranked, in document/reading order, not a
    relevance-filtered subset.
    """

    def setUp(self):
        self.doc_a = make_document("Doc A")
        self.doc_b = make_document("Doc B")
        # Deliberately created out of chunk_index order, to prove ordering
        # comes from the query, not insertion order.
        self.a2 = make_chunk(self.doc_a, "a-second", unit_vector(1), chunk_index=1)
        self.a1 = make_chunk(self.doc_a, "a-first", unit_vector(0), chunk_index=0)
        self.b1 = make_chunk(self.doc_b, "b-first", unit_vector(0), chunk_index=0)

    def test_returns_every_chunk_across_all_documents_by_default(self):
        results = get_all_chunks()
        self.assertEqual({r.chunk.id for r in results}, {self.a1.id, self.a2.id, self.b1.id})

    def test_ordered_by_document_then_chunk_index(self):
        results = get_all_chunks()
        contents = [r.chunk.content for r in results if r.chunk.document_id == self.doc_a.id]
        self.assertEqual(contents, ["a-first", "a-second"])

    def test_document_ids_filter_restricts_to_that_document(self):
        results = get_all_chunks(document_ids=[self.doc_a.id])
        self.assertEqual({r.chunk.id for r in results}, {self.a1.id, self.a2.id})

    def test_scores_are_not_relevance_ranked(self):
        # No query was matched against these chunks, so every score is the
        # same fixed sentinel rather than a real relevance signal.
        results = get_all_chunks()
        self.assertTrue(all(r.score == 1.0 for r in results))

    def test_empty_database_returns_empty_list(self):
        Chunk.objects.all().delete()
        self.assertEqual(get_all_chunks(), [])
