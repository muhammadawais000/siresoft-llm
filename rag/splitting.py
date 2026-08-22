"""Turn loaded LangChain Documents into chunk dicts ready for the Chunk model.

Chunk size/overlap are token-based (via tiktoken's cl100k_base encoding,
used as a length function rather than a literal tokenizer match for every
possible Ollama model — a close, consistent proxy across models is more
useful here than being exactly right for one). Both are read from Django
settings, never hardcoded, so ops can retune without a code change.
"""

from functools import lru_cache

from django.conf import settings
from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from documents.models import Document

_MARKDOWN_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


@lru_cache(maxsize=1)
def _tiktoken_encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def token_len(text: str) -> int:
    return len(_tiktoken_encoding().encode(text))


def _size_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        length_function=token_len,
        # Ordered to respect structure first: paragraphs, then lines, then
        # sentences, then words, only falling to a hard character cut as
        # a last resort.
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def split_document(document: Document, lc_docs: list[LCDocument]) -> list[dict]:
    if document.source_type == Document.SourceType.MARKDOWN:
        split_docs = _split_markdown(lc_docs)
    else:
        split_docs = _size_splitter().split_documents(lc_docs)

    chunks = []
    for i, doc in enumerate(split_docs):
        content = doc.page_content.strip()
        if not content:
            continue
        chunks.append(
            {
                "content": content,
                "chunk_index": i,
                "page_number": doc.metadata.get("page_number"),
                "section_heading": doc.metadata.get("section_heading", ""),
                "token_count": token_len(content),
            }
        )
    return chunks


def _split_markdown(lc_docs: list[LCDocument]) -> list[LCDocument]:
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS, strip_headers=False
    )

    sections: list[LCDocument] = []
    for doc in lc_docs:
        for section in header_splitter.split_text(doc.page_content):
            heading = " > ".join(
                section.metadata[key] for key in ("h1", "h2", "h3") if key in section.metadata
            )
            section.metadata["section_heading"] = heading
            section.metadata["page_number"] = None
            sections.append(section)

    if not sections:
        # No headers found at all — fall back to treating the whole file
        # as one section rather than silently dropping content.
        sections = lc_docs

    # Headers give us section boundaries; still enforce max chunk size
    # within each section so long sections don't exceed CHUNK_SIZE.
    return _size_splitter().split_documents(sections)
