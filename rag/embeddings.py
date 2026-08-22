"""Self-hosted embedding model access.

The model is loaded once per process and cached (`lru_cache`), not
re-instantiated per call — re-loading a sentence-transformers model per
request is the most common performance bug in RAG code, and the brief
calls it out explicitly. Celery worker processes additionally warm this
cache at process boot (see `rag.apps.RagConfig.ready`) so the first
ingestion task isn't slowed by a cold load.
"""

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_embedding_model():
    from langchain_huggingface import HuggingFaceEmbeddings

    profile = settings.EMBEDDING_PROFILES[settings.EMBEDDING_PROFILE]
    logger.info("Loading embedding model %s (this happens once per process)", profile["model_name"])

    encode_kwargs = {
        "normalize_embeddings": True,
        "batch_size": settings.EMBEDDING_BATCH_SIZE,
    }
    # BGE models need an instruction prefix on queries but NOT on the
    # passages/chunks being indexed. `query_encode_kwargs` is what
    # langchain_huggingface's embed_query() uses instead of encode_kwargs
    # (falling back to encode_kwargs when empty) — passed straight through
    # to SentenceTransformer.encode(prompt=...), which prepends it before
    # embedding. embed_documents() always uses plain encode_kwargs, so
    # passages never get the instruction. For profiles with no instruction
    # (e.g. minilm) this is just an empty dict, i.e. no special-casing.
    query_encode_kwargs = dict(encode_kwargs)
    if profile["query_instruction"]:
        query_encode_kwargs["prompt"] = profile["query_instruction"]

    return HuggingFaceEmbeddings(
        model_name=profile["model_name"],
        model_kwargs={"device": "cpu"},
        encode_kwargs=encode_kwargs,
        query_encode_kwargs=query_encode_kwargs,
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed a batch of chunk texts for indexing (no query instruction)."""
    if not texts:
        return []
    return get_embedding_model().embed_documents(texts)


def embed_query(text: str) -> list[float]:
    """Embed a user query for retrieval (BGE instruction applied automatically)."""
    return get_embedding_model().embed_query(text)
