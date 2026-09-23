"""Web search fallback: only called when document retrieval finds nothing
relevant. Wraps the `duckduckgo-search` library so the rest of the app
never talks to it directly -- if the provider changes later (SearXNG,
Tavily), only this file needs to change.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException

logger = logging.getLogger(__name__)


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str

    def to_citation(self, rank: int) -> dict:
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
    """Best-effort search. Returns [] on any failure, or when the feature
    is disabled -- callers treat that exactly like retrieve() returning
    no chunks (i.e. fall through to the existing NO_CONTEXT_MESSAGE).
    """
    if not settings.WEB_SEARCH_ENABLED:
        return []

    max_results = max_results or settings.WEB_SEARCH_MAX_RESULTS
    try:
        raw_results = DDGS().text(query, max_results=max_results)
    except DuckDuckGoSearchException:
        logger.exception("web_search: DuckDuckGo request failed for query=%r", query)
        return []
    except Exception:
        logger.exception("web_search: unexpected error for query=%r", query)
        return []

    return [
        WebResult(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
        for r in raw_results
        if r.get("href")
    ]