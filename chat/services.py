"""Orchestrates one chat turn: history-aware rewrite -> hybrid retrieval ->
grounded generation -> persistence, yielding SSE-shaped events as it goes.

This is the one place that ties the `rag` toolkit (retrieval + generation)
to `chat`'s sessions/messages — `rag` itself stays unaware that chat
sessions exist.
"""

import logging
import re
import time

from django.conf import settings
from django.utils import timezone
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from chat.models import ChatMessage, ChatSession, MessageCitation
from core.exceptions import OllamaUnavailableError
from core.ollama import get_installed_models
from rag.generation import ANSWER_PROMPT, SUMMARY_PROMPT, condense_question, get_llm, stream_answer
from rag.retrieval import get_all_chunks, retrieve

logger = logging.getLogger(__name__)

NO_CONTEXT_MESSAGE = (
    "I couldn't find anything in the uploaded documents relevant to that question. "
    "Try rephrasing, or upload a document that covers this topic."
)

# Whole-document requests ("summarize this", "what is this file about")
# need every chunk of the document, not hybrid retrieval's top-k semantic
# match against a query embedding -- a summary query doesn't resemble the
# text it needs to pull in, the way a factual question does.
_SUMMARY_INTENT_RE = re.compile(
    r"\b(summar(y|ize|ise)|overview|recap)\b"
    r"|\bwhat('?s| is) (this|the) (file|document|upload)s?\b.*\babout\b"
    r"|\bexplain (this|the) (file|document)\b",
    re.IGNORECASE,
)


def _is_summary_request(question: str) -> bool:
    return bool(_SUMMARY_INTENT_RE.search(question))


def _history_as_messages(session: ChatSession) -> list[BaseMessage]:
    turns = settings.CHAT_HISTORY_TURNS * 2  # user + assistant per turn
    recent = list(session.messages.order_by("-created_at")[:turns])
    recent.reverse()
    return [
        HumanMessage(content=m.content) if m.role == ChatMessage.Role.USER else AIMessage(content=m.content)
        for m in recent
    ]


def stream_chat_turn(session: ChatSession, question: str, model_name: str, document_ids=None):
    """Generator of `{"event": ..., "data": ...}` dicts for one chat turn.

    Persists the user message immediately and the assistant message (with
    citations) once generation completes. Raises OllamaUnavailableError
    up-front if the requested model isn't actually installed, before any
    retrieval work happens.
    """
    if model_name not in settings.OLLAMA_MODELS:
        raise ValueError(f"'{model_name}' is not one of the configured models.")
    if model_name not in get_installed_models():
        raise OllamaUnavailableError(
            f"Model '{model_name}' is not installed on the Ollama server. "
            f"Run `ollama pull {model_name}` first."
        )

    history = _history_as_messages(session)

    if not session.title:
        session.title = question[:80]
    session.save(update_fields=["title", "updated_at"])

    ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=question)

    start = time.monotonic()
    llm = get_llm(model_name)
    rewritten_query = condense_question(llm, question, history)
    yield {"event": "query_rewrite", "data": {"rewritten_query": rewritten_query}}

    is_summary = _is_summary_request(question)
    if is_summary:
        # Bypass relevance ranking entirely -- a summary needs the whole
        # document, not a top-k match against the query.
        results = get_all_chunks(document_ids=document_ids)
        answer_prompt = SUMMARY_PROMPT
    else:
        results = retrieve(rewritten_query, document_ids=document_ids)
        answer_prompt = ANSWER_PROMPT
        if not results and document_ids:
            # The cross-encoder is calibrated against prose passages; a
            # short document like a resume (name/contact block, bullet
            # lists) can score every chunk below the relevance threshold
            # for an extractive query ("what's the name") even when the
            # right chunk is right there. For a small enough scoped
            # document, falling back to full-document context costs
            # little and avoids a false "nothing found".
            fallback_chunks = get_all_chunks(document_ids=document_ids)
            if fallback_chunks and len(fallback_chunks) <= settings.RETRIEVAL_TOP_K:
                results = fallback_chunks

    if not results:
        latency_ms = int((time.monotonic() - start) * 1000)
        assistant_message = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.ASSISTANT,
            content=NO_CONTEXT_MESSAGE,
            model_used=model_name,
            latency_ms=latency_ms,
            rewritten_query=rewritten_query,
        )
        logger.info(
            "chat turn: session=%s model=%s latency_ms=%d chunks=[] query=%r -> no relevant context",
            session.id, model_name, latency_ms, question,
        )
        yield {"event": "token", "data": NO_CONTEXT_MESSAGE}
        yield {"event": "done", "data": {"message_id": assistant_message.id, "citations": []}}
        return

    full_answer = ""
    for token in stream_answer(model_name, rewritten_query, results, prompt=answer_prompt):
        full_answer += token
        yield {"event": "token", "data": token}

    latency_ms = int((time.monotonic() - start) * 1000)
    assistant_message = ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=full_answer,
        model_used=model_name,
        latency_ms=latency_ms,
        rewritten_query=rewritten_query,
    )
    # One enumeration builds both the persisted MessageCitation rows and the
    # SSE payload, so the `rank` assigned to each is guaranteed identical
    # in both places (and matches the [N] markers the LLM was told to cite).
    citation_rows = []
    citation_payload = []
    for rank, r in enumerate(results, start=1):
        citation_rows.append(MessageCitation(message=assistant_message, chunk=r.chunk, rank=rank, score=r.score))
        citation_payload.append(r.to_citation(rank))
    MessageCitation.objects.bulk_create(citation_rows)

    session.updated_at = timezone.now()
    session.save(update_fields=["updated_at"])

    logger.info(
        "chat turn: session=%s model=%s latency_ms=%d chunks=%s query=%r",
        session.id, model_name, latency_ms, [r.chunk.id for r in results], question,
    )

    yield {
        "event": "done",
        "data": {"message_id": assistant_message.id, "citations": citation_payload},
    }
