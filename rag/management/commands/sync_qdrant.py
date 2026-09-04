"""Re-embed every chunk in Postgres and (re)write its vector into Qdrant.

Needed the first time Qdrant is introduced into an existing deployment
(chunks already indexed against the old pgvector column have no vector in
Qdrant yet), and after wiping/losing the Qdrant volume -- Postgres/pgvector
never stored a vector Qdrant could just copy, so this re-runs the
embedding model over each chunk's `content` rather than migrating stored
vectors.

Usage:
    python manage.py sync_qdrant
"""

from django.core.management.base import BaseCommand

from rag.embeddings import embed_documents
from rag.models import Chunk
from rag.vector_store import upsert_chunks

BATCH_SIZE = 200


class Command(BaseCommand):
    help = "Re-embed every chunk and (re)write its vector into Qdrant."

    def handle(self, *args, **options):
        total = Chunk.objects.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No chunks in the database -- nothing to sync."))
            return

        self.stdout.write(f"Syncing {total} chunks to Qdrant in batches of {BATCH_SIZE}...")
        done = 0
        qs = Chunk.objects.order_by("document_id", "chunk_index").iterator(chunk_size=BATCH_SIZE)
        batch: list[Chunk] = []

        def flush(batch: list[Chunk]):
            if not batch:
                return
            vectors = embed_documents([c.content for c in batch])
            # Grouped by document so each Qdrant point's document_id payload
            # is correct even when a batch happens to straddle two documents.
            by_document: dict[int, tuple[list[int], list[list[float]]]] = {}
            for chunk, vector in zip(batch, vectors):
                ids, vecs = by_document.setdefault(chunk.document_id, ([], []))
                ids.append(chunk.id)
                vecs.append(vector)
            for document_id, (ids, vecs) in by_document.items():
                upsert_chunks(document_id, ids, vecs)

        for chunk in qs:
            batch.append(chunk)
            if len(batch) >= BATCH_SIZE:
                flush(batch)
                done += len(batch)
                self.stdout.write(f"  {done}/{total}")
                batch = []
        flush(batch)
        done += len(batch)

        self.stdout.write(self.style.SUCCESS(f"Synced {done} chunks to Qdrant."))
