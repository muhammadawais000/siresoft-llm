"""Cross-encoder re-ranking (BAAI/bge-reranker-base by default).

Same load-once-and-reuse discipline as `rag.embeddings`: the CrossEncoder
is expensive to instantiate, so it's cached per process rather than
per call.
"""

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder

    logger.info("Loading reranker model %s (this happens once per process)", settings.RERANKER_MODEL)
    return CrossEncoder(settings.RERANKER_MODEL, max_length=512, device="cpu")


def rerank(query: str, passages: list[str]) -> list[float]:
    """Score each passage's relevance to `query`, as a 0-1 probability.

    sentence-transformers' CrossEncoder already applies a Sigmoid activation
    by default for single-label (regression-style) reranker models like
    this one -- model.predict() returns calibrated 0-1 probabilities
    directly. Applying sigmoid again here would double-squash every score
    toward 0.5, destroying almost all of the discriminative signal between
    relevant and irrelevant passages (confirmed empirically: it collapsed
    every score into a ~0.0006-wide band around 0.5, regardless of
    relevance -- exactly the "everything looks equally (ir)relevant"
    symptom that motivated this fix).
    """
    if not passages:
        return []
    model = get_reranker()
    pairs = [[query, passage] for passage in passages]
    return [float(s) for s in model.predict(pairs)]
