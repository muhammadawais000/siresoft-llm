from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import GeneratedField
from pgvector.django import HnswIndex, VectorField

from documents.models import Document


class Chunk(models.Model):
    """A single embedded, retrievable unit of text.

    Two parallel indexes back retrieval: an HNSW index on `embedding` for
    dense/cosine search, and a GIN index on `content_tsv` for Postgres
    full-text keyword search. `content_tsv` is a database-generated column
    (see migration 0002) rather than something maintained in Python, so it
    can never drift out of sync with `content`.

    Chunks are immutable once created — re-ingesting a document deletes and
    recreates its chunks rather than updating them in place, which keeps
    chunk_index/embedding/content always consistent with each other.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")

    content = models.TextField()

    # Dimensionality is fixed at migration time from settings.EMBEDDING_DIMENSIONS.
    # Changing EMBEDDING_PROFILE requires a new migration (and a full
    # reindex) — this is intentional, per Document.embedding_model_name.
    embedding = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS, null=True)

    # Database-generated (STORED) column, maintained by Postgres itself on
    # every write to `content` — never written to directly from Python, and
    # Django excludes generated columns from INSERT/UPDATE automatically.
    content_tsv = GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    # --- Metadata, per the chunking spec ---
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField()
    section_heading = models.CharField(max_length=512, blank=True)
    token_count = models.IntegerField(null=True, blank=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "chunk_index"]
        indexes = [
            HnswIndex(
                name="chunk_embedding_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            GinIndex(fields=["content_tsv"], name="chunk_content_tsv_gin_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"], name="unique_chunk_index_per_document"
            ),
        ]

    def __str__(self):
        return f"{self.document.title} [{self.chunk_index}]"
