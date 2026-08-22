"""Upload validation and content-hashing, shared by all upload entry points."""

import hashlib
from pathlib import Path

from django.conf import settings

from core.exceptions import FileTooLargeError, UnsupportedFileTypeError
from documents.models import Document

EXTENSION_TO_SOURCE_TYPE = {
    ".pdf": Document.SourceType.PDF,
    ".docx": Document.SourceType.DOCX,
    ".txt": Document.SourceType.TEXT,
    ".md": Document.SourceType.MARKDOWN,
}


def validate_and_resolve_source_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS or ext not in EXTENSION_TO_SOURCE_TYPE:
        allowed = ", ".join(settings.ALLOWED_UPLOAD_EXTENSIONS)
        raise UnsupportedFileTypeError(f"'{ext}' is not supported. Allowed types: {allowed}")
    return EXTENSION_TO_SOURCE_TYPE[ext]


def validate_upload_size(uploaded_file) -> None:
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise FileTooLargeError(
            f"'{uploaded_file.name}' is {uploaded_file.size / 1024 / 1024:.1f}MB, "
            f"which exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit."
        )


def compute_file_sha256(uploaded_file) -> str:
    hasher = hashlib.sha256()
    for block in uploaded_file.chunks():
        hasher.update(block)
    uploaded_file.seek(0)
    return hasher.hexdigest()


def compute_url_sha256(url: str) -> str:
    # URLs don't have bytes to hash until fetched, so the normalized URL
    # string itself is what we dedupe on.
    normalized = url.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
