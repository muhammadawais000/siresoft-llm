from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


def upload_to(instance, filename):
    return f"documents/{instance.sha256[:2]}/{instance.sha256}/{filename}"


class Document(TimeStampedModel):
    """A single knowledge source: an uploaded file or a fetched URL.

    Ingestion progresses through STATUS_CHOICES as the Celery pipeline runs.
    `error_message` is only populated on FAILED and is meant to be shown
    directly to the user, so ingestion code should keep it short and
    actionable rather than dumping a traceback into it.
    """

    class SourceType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word Document"
        TEXT = "txt", "Plain Text"
        MARKDOWN = "md", "Markdown"
        URL = "url", "Web Page"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PARSING = "parsing", "Parsing"
        CHUNKING = "chunking", "Chunking"
        EMBEDDING = "embedding", "Embedding"
        INDEXED = "indexed", "Indexed"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=512)
    source_type = models.CharField(max_length=8, choices=SourceType.choices)

    # Populated for file uploads; blank for URL sources.
    file = models.FileField(upload_to=upload_to, blank=True, null=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    # Populated for URL sources; blank for file uploads.
    source_url = models.URLField(max_length=2048, blank=True)

    # SHA-256 of the raw file bytes (or of the normalized URL for web
    # sources), used to detect and skip duplicate uploads.
    sha256 = models.CharField(max_length=64, db_index=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    error_message = models.TextField(blank=True)

    # Stamped once embedding actually happens. Retrieval code should refuse
    # to compare embeddings across documents with different values here.
    embedding_model_name = models.CharField(max_length=256, blank=True)

    page_count = models.IntegerField(null=True, blank=True)
    chunk_count = models.IntegerField(default=0)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )

    indexed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["sha256"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            # Duplicate detection is scoped to the whole library, not
            # per-user, since re-embedding the same file twice wastes
            # compute regardless of who uploaded it.
            models.UniqueConstraint(fields=["sha256"], name="unique_document_sha256"),
        ]

    def __str__(self):
        return self.title
