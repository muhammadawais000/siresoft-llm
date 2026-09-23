"""Web search fallback: only called when document retrieval finds nothing
relevant. Talks to a self-hosted SearXNG instance (see docker-compose.yml
and docker/searxng/settings.yml) over its JSON API -- no third-party
search API key/account/cost, consistent with every other component in
this stack (Ollama, embeddings, reranker) running fully locally.
"""

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str

    def to_citation(self, rank: int) -> dict:
        # Shaped to match RetrievedChunk.to_citation()'s keys where they
        # overlap (rank, content, score) so the frontend can render either
        # kind of citation without a separate code path -- chunk_id/
        # document_id are always None here since a web result has no chunk.
        return {
            "rank": rank,
            "chunk_id": None,
            "document_id": None,
            "document_title": self.title,
            "source_url": self.url,
            "content": self.snippet,
            "score": None,
        }


def web_search(query: str, max_results: int | None = None) -> list[WebResult]:
    """Best-effort search against the self-hosted SearXNG instance.

    Returns [] on any failure (SearXNG down, network error, malformed
    response) or when the feature is disabled -- callers treat that
    exactly like retrieve() returning no chunks, i.e. fall through to the
    existing NO_CONTEXT_MESSAGE rather than raising.
    """
    if not settings.WEB_SEARCH_ENABLED:
        return []

    max_results = max_results or settings.WEB_SEARCH_MAX_RESULTS
    try:
        resp = requests.get(
            f"{settings.SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=settings.WEB_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        raw_results = resp.json().get("results", [])
    except requests.RequestException:
        logger.exception("web_search: SearXNG request failed for query=%r", query)
        return []
    except ValueError:
        logger.exception("web_search: SearXNG returned non-JSON response for query=%r", query)
        return []

    return [
        WebResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
        for r in raw_results[:max_results]
        if r.get("url")
    ]
