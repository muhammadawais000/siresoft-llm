from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import GeneratedField

from documents.models import Document


class Chunk(models.Model):
    """A single retrievable unit of text.

    This table holds content and metadata only — the embedding vector
    itself lives in Qdrant (see rag.vector_store), keyed by this row's
    primary key. A GIN index on `content_tsv` backs Postgres full-text
    keyword search. `content_tsv` is a database-generated column (see
    migration 0002) rather than something maintained in Python, so it can
    never drift out of sync with `content`.

    Chunks are immutable once created — re-ingesting a document deletes and
    recreates its chunks (and their Qdrant vectors) rather than updating
    them in place, which keeps chunk_index/content always consistent.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")

    content = models.TextField()

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
            GinIndex(fields=["content_tsv"], name="chunk_content_tsv_gin_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"], name="unique_chunk_index_per_document"
            ),
        ]

    def __str__(self):
        return f"{self.document.title} [{self.chunk_index}]"
