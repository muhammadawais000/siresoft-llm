"""Turn a Document (file or URL) into a list of LangChain Documents.

Each function returns `langchain_core.documents.Document` objects with at
minimum a `page` (0-indexed, PDFs only) or nothing in metadata — page/section
metadata is normalized onto our own Chunk model later, in `rag.splitting`.

Every loader raises `core.exceptions.IngestionError` (or a subclass) with a
short, user-facing message on failure rather than letting a library
exception bubble up — that message is what ends up in
`Document.error_message` and is shown directly in the UI.
"""

import logging

from langchain_core.documents import Document as LCDocument

from core.exceptions import EmptyDocumentError, IngestionError, UnsupportedFileTypeError
from documents.models import Document

logger = logging.getLogger(__name__)


def load_document(document: Document) -> list[LCDocument]:
    if document.source_type == Document.SourceType.PDF:
        return _load_pdf(document.file.path)
    if document.source_type == Document.SourceType.DOCX:
        return _load_docx(document.file.path)
    if document.source_type in (Document.SourceType.TEXT, Document.SourceType.MARKDOWN):
        return _load_text(document.file.path)
    if document.source_type == Document.SourceType.URL:
        return _load_url(document.source_url)
    raise UnsupportedFileTypeError(f"No loader registered for source type {document.source_type!r}")


def _total_chars(docs: list[LCDocument]) -> int:
    return sum(len(d.page_content.strip()) for d in docs)


def _load_pdf(path: str) -> list[LCDocument]:
    from django.conf import settings
    from langchain_community.document_loaders import PyMuPDFLoader

    try:
        docs = PyMuPDFLoader(path).load()
    except Exception as exc:
        raise IngestionError(f"Could not open PDF: {exc}") from exc

    if not docs:
        raise EmptyDocumentError("PDF has no pages.")

    avg_chars_per_page = _total_chars(docs) / len(docs)
    if avg_chars_per_page < settings.OCR_MIN_CHARS_PER_PAGE:
        logger.info(
            "PDF %s looks scanned (%.1f chars/page) — falling back to OCR", path, avg_chars_per_page
        )
        docs = _ocr_pdf(path)

    if _total_chars(docs) == 0:
        raise EmptyDocumentError(
            "No extractable text found, even after OCR. The PDF may be blank or unreadable."
        )

    # PyMuPDFLoader's `page` metadata is 0-indexed; normalize to 1-indexed
    # for display, matching how humans refer to page numbers.
    for doc in docs:
        doc.metadata["page_number"] = doc.metadata.get("page", 0) + 1

    return docs


def _ocr_pdf(path: str) -> list[LCDocument]:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise IngestionError(
            "OCR fallback requires the poppler-utils and tesseract-ocr system "
            "packages to be installed on the server."
        ) from exc

    try:
        images = convert_from_path(path)
    except Exception as exc:
        raise IngestionError(f"Could not rasterize PDF pages for OCR: {exc}") from exc

    docs = []
    try:
        for i, image in enumerate(images):
            text = pytesseract.image_to_string(image)
            docs.append(LCDocument(page_content=text, metadata={"page": i, "page_number": i + 1}))
    except pytesseract.TesseractNotFoundError as exc:
        # pytesseract imports fine even when the `tesseract` binary itself
        # isn't installed — the failure only surfaces here, on first use.
        raise IngestionError(
            "OCR fallback requires the tesseract-ocr system package to be "
            "installed on the server (the tesseract binary was not found)."
        ) from exc
    return docs


def _load_docx(path: str) -> list[LCDocument]:
    from langchain_community.document_loaders import Docx2txtLoader

    try:
        docs = Docx2txtLoader(path).load()
    except Exception as exc:
        raise IngestionError(f"Could not open DOCX file: {exc}") from exc

    if _total_chars(docs) == 0:
        raise EmptyDocumentError("DOCX file has no extractable text.")

    # DOCX has no reliable page concept at the text-extraction layer.
    for doc in docs:
        doc.metadata["page_number"] = None

    return docs


def _load_text(path: str) -> list[LCDocument]:
    import chardet

    with open(path, "rb") as f:
        raw = f.read()

    if not raw.strip():
        raise EmptyDocumentError("File is empty.")

    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        # Never crash on a bad encoding guess — fall back to a lossy decode
        # so the document still gets indexed rather than failing outright.
        logger.warning("Falling back to lossy utf-8 decode for %s (detected %s)", path, encoding)
        text = raw.decode("utf-8", errors="replace")

    return [LCDocument(page_content=text, metadata={"page_number": None})]


def _load_url(url: str) -> list[LCDocument]:
    import trafilatura

    try:
        downloaded = trafilatura.fetch_url(url)
    except Exception as exc:
        raise IngestionError(f"Could not fetch URL: {exc}") from exc

    if downloaded is None:
        raise IngestionError(f"Could not fetch URL (unreachable or blocked): {url}")

    text = trafilatura.extract(
        downloaded, include_comments=False, include_tables=True, favor_precision=True
    )
    if not text or not text.strip():
        raise EmptyDocumentError("No article content could be extracted from this page.")

    return [LCDocument(page_content=text, metadata={"page_number": None, "source": url})]
