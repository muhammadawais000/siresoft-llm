"""Shared helper for checking which Ollama models are actually installed.

Used by the chat model dropdown (disable anything not pulled) and by the
generation chain (refuse to call a model that isn't there instead of
letting Ollama return a confusing 404 mid-stream).
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_cache: dict = {"models": None, "fetched_at": 0.0}


def get_installed_models() -> set[str]:
    """Return the set of model tags currently pulled on the Ollama server.

    Cached with a short TTL rather than per-request (hitting /api/tags on
    every chat message would be wasteful) or forever (a freshly `ollama
    pull`ed model should show up in the dropdown without a server restart).
    Falls back to the last known-good result if Ollama is unreachable,
    rather than blanking the dropdown on a transient network blip.
    """
    now = time.monotonic()
    if _cache["models"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["models"]

    try:
        response = requests.get(
            f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=settings.OLLAMA_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        tags = {model["name"] for model in response.json().get("models", [])}
    except requests.RequestException as exc:
        logger.warning("Could not reach Ollama at %s: %s", settings.OLLAMA_BASE_URL, exc)
        tags = _cache["models"] if _cache["models"] is not None else set()

    _cache["models"] = tags
    _cache["fetched_at"] = now
    return tags
