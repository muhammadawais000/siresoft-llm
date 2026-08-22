"""Shared exception types used across the ingestion and RAG pipelines.

Centralizing these lets Celery tasks and API views catch/report failures
uniformly instead of each app inventing its own error shape.
"""


class SiresoftRAGError(Exception):
    """Base class for all application-raised (as opposed to library) errors."""


class UnsupportedFileTypeError(SiresoftRAGError):
    """Raised when an uploaded file's extension isn't in ALLOWED_UPLOAD_EXTENSIONS."""


class FileTooLargeError(SiresoftRAGError):
    """Raised when an uploaded file exceeds MAX_UPLOAD_SIZE_MB."""


class IngestionError(SiresoftRAGError):
    """Raised when a document fails to parse, chunk, or embed.

    The message is written to Document.error_message, so it should be
    short and human-readable rather than a raw traceback.
    """


class EmptyDocumentError(IngestionError):
    """Raised when a loader yields no extractable text (e.g. a scanned PDF
    that OCR also failed to recover text from). We refuse to silently index
    an empty document.
    """


class OllamaUnavailableError(SiresoftRAGError):
    """Raised when the Ollama server is unreachable or a requested model
    isn't installed there.
    """
