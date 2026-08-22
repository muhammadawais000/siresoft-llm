"""LCEL chains for the RAG generation step: history-aware query rewriting
and grounded answer synthesis via ChatOllama.

Model selection is a plain function argument threaded all the way from the
API request to `ChatOllama(model=...)` — whatever the user picked in the
dropdown is what actually generates the answer, per the project brief.
"""

from django.conf import settings
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_ollama import ChatOllama

from rag.retrieval import RetrievedChunk

CONDENSE_QUESTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Given the conversation history and a follow-up question, rewrite the "
            "follow-up into a standalone question that includes all context needed "
            "to understand it without the history. If it is already standalone, "
            "return it unchanged. Output only the rewritten question, nothing else.",
        ),
        MessagesPlaceholder("history"),
        ("human", "{question}"),
    ]
)

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful assistant that answers questions using ONLY the "
            "context provided below, drawn from the user's own uploaded documents.\n\n"
            "Rules:\n"
            "1. Answer strictly from the context. Never use outside knowledge, and "
            "never invent or guess facts that aren't in it.\n"
            "2. If the context doesn't contain enough information to answer, say so "
            "plainly (e.g. \"The documents don't cover that\") instead of guessing.\n"
            "3. Cite sources inline using the bracketed numbers from the context "
            "(e.g. [1], [2]) next to the claims they support.\n"
            "4. Be thorough and detailed: include every relevant fact, figure, and "
            "piece of surrounding context the source material gives you for this "
            "question — don't compress a multi-part answer down to one line. Use "
            "full sentences and multiple paragraphs or a list where that helps "
            "readability. Only stay brief if the honest answer genuinely is brief "
            "(e.g. a single fact with nothing more to add).\n\n"
            "Context:\n{context}",
        ),
        ("human", "{question}"),
    ]
)

# Used instead of ANSWER_PROMPT when the question is a whole-document
# request ("summarize this", "what is this file about") -- see
# chat.services._is_summary_request. The context passed alongside this
# prompt is every chunk of the document(s) in scope, not a relevance-ranked
# top-k, so the instructions are written for that shape of input.
SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful assistant. Below is the full content of one or more "
            "documents, provided in order as numbered sections.\n\n"
            "Write a thorough, well-organized summary covering every major section "
            "and point in the material — not just the first part. Use full "
            "sentences and paragraphs (or short headed sections for a long, "
            "multi-topic document). Only summarize what's actually in the text "
            "below; never add outside knowledge or invented detail. Cite sources "
            "inline using the bracketed numbers (e.g. [1], [2]) next to the points "
            "they support.\n\n"
            "Document content:\n{context}",
        ),
        ("human", "{question}"),
    ]
)


def get_llm(model_name: str) -> ChatOllama:
    return ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=model_name,
        temperature=settings.GENERATION_TEMPERATURE,
    )


def condense_question(llm: ChatOllama, question: str, history: list[BaseMessage]) -> str:
    """Rewrite a follow-up question into a standalone one, using chat history.

    Skipped entirely (returns `question` unchanged) when there's no history
    yet — the first message in a session is standalone by definition, and
    it saves an extra LLM round-trip on every session's opening message.
    """
    if not history:
        return question
    chain = CONDENSE_QUESTION_PROMPT | llm | StrOutputParser()
    rewritten = chain.invoke({"question": question, "history": history})
    return rewritten.strip() or question


def format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, retrieved in enumerate(chunks, start=1):
        chunk = retrieved.chunk
        location = f"page {chunk.page_number}" if chunk.page_number else chunk.section_heading
        header = f"[{i}] {chunk.document.title}" + (f" ({location})" if location else "")
        parts.append(f"{header}\n{chunk.content}")
    return "\n\n".join(parts)


def stream_answer(
    model_name: str,
    question: str,
    context_chunks: list[RetrievedChunk],
    prompt: ChatPromptTemplate = ANSWER_PROMPT,
):
    """Yield answer tokens as they're generated by the selected Ollama model.

    `prompt` defaults to the grounded Q&A prompt; pass SUMMARY_PROMPT for
    whole-document summarization requests, which need different framing
    for the same {question}/{context} shape.
    """
    llm = get_llm(model_name)
    chain = prompt | llm | StrOutputParser()
    context = format_context(context_chunks)
    yield from chain.stream({"question": question, "context": context})
