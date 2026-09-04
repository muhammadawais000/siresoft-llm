"""Retrieval quality proof: dense-only vs. hybrid+RRF vs. hybrid+RRF+rerank.

Loads a small, deliberately adversarial corpus, runs a handful of queries
against all three retrieval strategies, and reports whether each strategy
puts the actually-correct chunk at rank 1. The corpus is built around cases
dense (embedding-only) search is known to struggle with — exact error
codes, product SKUs, and a "distractor" document that shares vocabulary
with the correct answer but not the specific fact being asked about —
which is exactly the scenario the project brief calls out hybrid search
and re-ranking for.

Usage:
    python manage.py prove_retrieval          # runs the proof, cleans up after
    python manage.py prove_retrieval --keep   # leaves the corpus in the DB
"""

import uuid

from django.core.management.base import BaseCommand

from documents.models import Document
from rag.embeddings import embed_query
from rag.indexing import embed_chunks, persist_chunks
from rag.models import Chunk
from rag.retrieval import dense_search, reciprocal_rank_fusion, retrieve, sparse_search
from rag.splitting import token_len

# --------------------------------------------------------------------------
# Corpus: each document is a list of standalone passages (one passage = one
# chunk, so we control exactly what's retrievable — chunking itself is
# already proven correct in Phase 2's ingestion tests).
# --------------------------------------------------------------------------

CORPUS = {
    "Industrial Sensor Manual": [
        "The SG-9000 industrial sensor operates in temperatures from -40C to 85C "
        "with an accuracy of plus or minus 0.5 percent of reading.",
        "Error code E-4471 indicates a calibration drift fault. Recalibrate the "
        "sensor using the CAL-2 procedure and replace the reference probe if the "
        "fault recurs after recalibration.",
        "Error code E-2210 indicates a power supply undervoltage condition. Check "
        "the input voltage is within the rated 18-30VDC range before replacing "
        "any components.",
        "Warranty terms are 24 months from the date of purchase. Contact support "
        "for RMA requests on any unit still under warranty.",
    ],
    "General Troubleshooting Guide": [
        # Deliberate distractor: shares "calibration"/"error"/"sensor" vocabulary
        # with the real answer above, but never mentions the actual code.
        "If a device reports an unexpected error, first check the calibration "
        "history and confirm the sensor was serviced within the last twelve "
        "months before escalating to engineering.",
        "Most calibration faults are resolved by power-cycling the unit and "
        "re-running the automatic self-test sequence from the diagnostics menu.",
        # Stronger distractor: echoes the wording of the voltage query almost
        # verbatim (voltage range, replacing components) without ever stating
        # the actual number — the kind of fluent paraphrase dense embeddings
        # tend to over-rate relative to a terse, jargon-heavy factual answer.
        "Before replacing any components on industrial equipment, always verify "
        "the power supply is delivering stable voltage within the manufacturer's "
        "specified operating range, since undervoltage is a common root cause of "
        "intermittent equipment faults.",
    ],
    "Employee Handbook": [
        "Employees accrue 15 days of paid vacation per year, credited monthly. "
        "Unused days roll over up to a maximum of 5 days into the following year.",
        "All expense reports must be submitted within 30 days of the purchase "
        "date. Reports submitted after this window require manager approval.",
    ],
    "Weekend Recipe Newsletter": [
        # Pure noise: should never surface for any of the test queries.
        "This week's recipe is a slow-braised short rib with roasted root "
        "vegetables and a red wine reduction, finished with fresh thyme.",
    ],
}

# (query, substring that must appear in the single correct chunk)
QUERIES = [
    (
        "What does error code E-4471 mean and how do I fix it?",
        "E-4471",
    ),
    (
        "How many vacation days do new employees get per year?",
        "15 days of paid vacation",
    ),
    (
        "What voltage range does the sensor expect before I replace parts?",
        "18-30VDC",
    ),
]


class Command(BaseCommand):
    help = "Prove hybrid retrieval + reranking outperforms dense-only cosine search."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Leave the proof corpus in the database instead of deleting it afterwards.",
        )

    def handle(self, *args, **options):
        documents = self._ingest_corpus()
        try:
            self._run_queries()
        finally:
            if options["keep"]:
                self.stdout.write(self.style.WARNING(f"\nKeeping {len(documents)} proof documents (--keep)."))
            else:
                for doc in documents:
                    doc.delete()
                self.stdout.write(self.style.WARNING(f"\nCleaned up {len(documents)} proof documents."))

    # -- corpus setup ------------------------------------------------------

    def _ingest_corpus(self) -> list[Document]:
        self.stdout.write("Indexing proof corpus...")
        documents = []
        for title, passages in CORPUS.items():
            doc = Document.objects.create(
                title=title,
                source_type=Document.SourceType.TEXT,
                sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                status=Document.Status.EMBEDDING,
            )
            chunk_dicts = [
                {
                    "content": passage,
                    "chunk_index": i,
                    "page_number": None,
                    "section_heading": "",
                    "token_count": token_len(passage),
                }
                for i, passage in enumerate(passages)
            ]
            vectors = embed_chunks(chunk_dicts)
            persist_chunks(doc, chunk_dicts, vectors)
            doc.status = Document.Status.INDEXED
            doc.save()
            documents.append(doc)
        self.stdout.write(self.style.SUCCESS(f"Indexed {len(documents)} documents.\n"))
        return documents

    # -- proof ---------------------------------------------------------------

    def _run_queries(self):
        all_passed = True
        for query, needle in QUERIES:
            self.stdout.write(self.style.MIGRATE_HEADING(f'Query: "{query}"'))

            dense_top5 = self._dense_only_baseline(query, top_n=5)
            self._print_ranking("  Dense-only (cosine, no rerank)", dense_top5, needle)

            rrf_top5 = self._hybrid_prerank(query, top_n=5)
            self._print_ranking("  Hybrid RRF (pre-rerank)", rrf_top5, needle)

            final = retrieve(query, top_k=5)
            final_pairs = [(r.chunk.content, r.score) for r in final]
            self._print_ranking("  Hybrid RRF + cross-encoder rerank (final)", final_pairs, needle)

            passed = bool(final_pairs) and needle in final_pairs[0][0]
            all_passed &= passed
            status = self.style.SUCCESS("PASS") if passed else self.style.ERROR("FAIL")
            self.stdout.write(f"  -> correct chunk ranked #1 after full pipeline: {status}\n")

        summary = self.style.SUCCESS("ALL QUERIES PASSED") if all_passed else self.style.ERROR("SOME QUERIES FAILED")
        self.stdout.write(summary)

    def _dense_only_baseline(self, query: str, top_n: int) -> list[tuple[str, float]]:
        from rag.vector_store import search_with_scores

        scored = search_with_scores(embed_query(query), limit=top_n)
        chunks_by_id = {c.id: c for c in Chunk.objects.filter(id__in=[cid for cid, _ in scored])}
        return [(chunks_by_id[cid].content, score) for cid, score in scored if cid in chunks_by_id]

    def _hybrid_prerank(self, query: str, top_n: int) -> list[tuple[str, float]]:
        dense_ids = dense_search(embed_query(query), pool_size=20)
        sparse_ids = sparse_search(query, pool_size=20)
        fused = reciprocal_rank_fusion(dense_ids, sparse_ids, k=60)
        ranked_ids = sorted(fused, key=fused.get, reverse=True)[:top_n]
        chunks_by_id = {c.id: c for c in Chunk.objects.filter(id__in=ranked_ids)}
        return [(chunks_by_id[cid].content, fused[cid]) for cid in ranked_ids if cid in chunks_by_id]

    def _print_ranking(self, label: str, pairs: list[tuple[str, float]], needle: str):
        self.stdout.write(label + ":")
        if not pairs:
            self.stdout.write("    (no results)")
            return
        for rank, (content, score) in enumerate(pairs, start=1):
            marker = " <-- correct chunk" if needle in content else ""
            snippet = content[:70].replace("\n", " ")
            self.stdout.write(f"    {rank}. [{score:.4f}] {snippet}...{marker}")
