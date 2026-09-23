"""Chat turn orchestration tests. retrieve/condense_question/get_llm/
stream_answer/get_installed_models are all mocked at the chat.services
boundary -- this suite is about services.py's own responsibilities
(model gating, no-context short-circuit, citation persistence, session
titling), not about retrieval or generation quality, which are covered
in rag/tests/.
"""

import uuid
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from chat.models import ChatMessage, ChatSession, MessageCitation
from chat.services import NO_CONTEXT_MESSAGE, _is_summary_request, stream_chat_turn
from core.exceptions import OllamaUnavailableError
from documents.models import Document
from rag.models import Chunk
from rag.retrieval import RetrievedChunk
from rag.retrieval import get_all_chunks as real_get_all_chunks
from rag.web_search import WebResult
from chat.serializers import MessageCitationSerializer


def make_chunk(content="content"):
    doc = Document.objects.create(
        title="Doc",
        source_type=Document.SourceType.TEXT,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=Document.Status.INDEXED,
    )
    return Chunk.objects.create(document=doc, content=content, chunk_index=0, embedding=[0.0] * settings.EMBEDDING_DIMENSIONS)


class StreamChatTurnTests(TestCase):
    def setUp(self):
        self.session = ChatSession.objects.create()

    @patch("chat.services.get_installed_models", return_value=set())
    def test_uninstalled_model_raises_before_any_persistence(self, mock_installed):
        with self.assertRaises(OllamaUnavailableError):
            list(stream_chat_turn(self.session, "hello", "llama3.1:8b"))
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_model_not_in_configured_list_raises_value_error(self):
        with self.assertRaises(ValueError):
            list(stream_chat_turn(self.session, "hello", "not-a-real-model"))
        self.assertEqual(ChatMessage.objects.count(), 0)

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_no_relevant_context_short_circuits_without_calling_generation(self, mock_get_llm, mock_condense, mock_installed):
        # web_search is mocked here too (even though WEB_SEARCH_ENABLED
        # defaults to False) so this test's outcome never depends on
        # whichever value happens to be set in the environment it runs
        # in -- see WebSearchFallbackTests for the dedicated web-search
        # behavior tests.
        with patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.web_search", return_value=[]
        ), patch("chat.services.stream_answer") as mock_stream_answer:
            events = list(stream_chat_turn(self.session, "irrelevant question", "llama3.1:8b"))
            mock_stream_answer.assert_not_called()

        event_types = [e["event"] for e in events]
        self.assertEqual(event_types, ["query_rewrite", "token", "done"])
        self.assertEqual(events[1]["data"], NO_CONTEXT_MESSAGE)
        self.assertEqual(events[2]["data"]["citations"], [])

        assistant = ChatMessage.objects.get(role=ChatMessage.Role.ASSISTANT)
        self.assertEqual(assistant.content, NO_CONTEXT_MESSAGE)
        self.assertEqual(assistant.model_used, "llama3.1:8b")

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_successful_turn_persists_citations_with_matching_rank(self, mock_get_llm, mock_condense, mock_installed):
        chunk = make_chunk("Employees get 15 days of vacation.")
        result = RetrievedChunk(chunk=chunk, score=0.73)

        with patch("chat.services.retrieve", return_value=[result]), patch(
            "chat.services.stream_answer", return_value=iter(["15 ", "days"])
        ):
            events = list(stream_chat_turn(self.session, "vacation days?", "llama3.1:8b"))

        done_event = next(e for e in events if e["event"] == "done")
        self.assertEqual(len(done_event["data"]["citations"]), 1)
        self.assertEqual(done_event["data"]["citations"][0]["rank"], 1)
        self.assertEqual(done_event["data"]["citations"][0]["chunk_id"], chunk.id)

        citation = MessageCitation.objects.get()
        self.assertEqual(citation.rank, 1)
        self.assertEqual(citation.chunk_id, chunk.id)
        self.assertEqual(citation.score, 0.73)

        assistant = ChatMessage.objects.get(role=ChatMessage.Role.ASSISTANT)
        self.assertEqual(assistant.content, "15 days")
        self.assertEqual(assistant.model_used, "llama3.1:8b")
        self.assertIsNotNone(assistant.latency_ms)
        self.assertEqual(assistant.rewritten_query, "vacation days?")

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_first_message_sets_a_blank_sessions_title(self, mock_get_llm, mock_condense, mock_installed):
        with patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.web_search", return_value=[]
        ), patch("chat.services.stream_answer", return_value=iter([])):
            list(stream_chat_turn(self.session, "What is the return policy?", "llama3.1:8b"))

        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "What is the return policy?")

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_existing_title_is_not_overwritten_by_later_turns(self, mock_get_llm, mock_condense, mock_installed):
        self.session.title = "Original title"
        self.session.save()

        with patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.web_search", return_value=[]
        ), patch("chat.services.stream_answer", return_value=iter([])):
            list(stream_chat_turn(self.session, "A completely different question", "llama3.1:8b"))

        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "Original title")

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    def test_history_gathers_prior_turns_in_chronological_order(self, mock_installed):
        ChatMessage.objects.create(session=self.session, role=ChatMessage.Role.USER, content="first question")
        ChatMessage.objects.create(session=self.session, role=ChatMessage.Role.ASSISTANT, content="first answer")

        captured = {}

        def fake_condense(llm, question, history):
            captured["history"] = [(m.type, m.content) for m in history]
            return question

        with patch("chat.services.condense_question", side_effect=fake_condense), patch(
            "chat.services.get_llm"
        ), patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.web_search", return_value=[]
        ), patch("chat.services.stream_answer", return_value=iter([])):
            list(stream_chat_turn(self.session, "a follow-up", "llama3.1:8b"))

        self.assertEqual(captured["history"], [("human", "first question"), ("ai", "first answer")])


class WebSearchFallbackTests(TestCase):
    """stream_chat_turn()'s last-resort path: when neither retrieve() nor
    the small-corpus fallback finds anything, rag.web_search.web_search()
    is tried before giving up with NO_CONTEXT_MESSAGE. web_search() and
    stream_web_answer() are mocked at the chat.services boundary --
    rag/tests/test_web_search.py covers web_search()'s own SearXNG
    integration.
    """

    def setUp(self):
        self.session = ChatSession.objects.create()

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_falls_back_to_web_search_when_nothing_found_in_documents(
        self, mock_get_llm, mock_condense, mock_installed
    ):
        web_result = WebResult(title="Example", url="https://example.com", snippet="An example snippet.")

        with patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.get_all_chunks", return_value=[]
        ), patch("chat.services.web_search", return_value=[web_result]), patch(
            "chat.services.stream_web_answer", return_value=iter(["Based on the web, ", "the answer is X."])
        ) as mock_stream_web:
            events = list(stream_chat_turn(self.session, "something not in any document", "llama3.1:8b"))

        mock_stream_web.assert_called_once()
        done_event = next(e for e in events if e["event"] == "done")
        self.assertEqual(done_event["data"]["source"], "web")
        self.assertEqual(len(done_event["data"]["citations"]), 1)
        self.assertEqual(done_event["data"]["citations"][0]["source_url"], "https://example.com")
        self.assertIsNone(done_event["data"]["citations"][0]["chunk_id"])

        citation = MessageCitation.objects.get()
        self.assertIsNone(citation.chunk)
        self.assertEqual(citation.source_url, "https://example.com")
        self.assertEqual(citation.source_title, "Example")
        self.assertIsNone(citation.score)

        assistant = ChatMessage.objects.get(role=ChatMessage.Role.ASSISTANT)
        self.assertEqual(assistant.content, "Based on the web, the answer is X.")

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_falls_through_to_no_context_message_when_web_search_also_empty(
        self, mock_get_llm, mock_condense, mock_installed
    ):
        with patch("chat.services.retrieve", return_value=[]), patch(
            "chat.services.get_all_chunks", return_value=[]
        ), patch("chat.services.web_search", return_value=[]), patch(
            "chat.services.stream_web_answer"
        ) as mock_stream_web:
            events = list(stream_chat_turn(self.session, "nothing anywhere", "llama3.1:8b"))

        mock_stream_web.assert_not_called()
        done_event = next(e for e in events if e["event"] == "done")
        self.assertEqual(done_event["data"]["citations"], [])
        assistant = ChatMessage.objects.get(role=ChatMessage.Role.ASSISTANT)
        self.assertEqual(assistant.content, NO_CONTEXT_MESSAGE)


class MessageCitationSerializerTests(TestCase):
    """A web citation has chunk=None -- MessageCitationSerializer must not
    try to dereference chunk.document_id/.content/etc on it (that used to
    raise AttributeError: 'NoneType' object has no attribute '...' the
    moment a session with a web-sourced answer was reloaded from history).
    """

    def setUp(self):
        self.session = ChatSession.objects.create()
        self.message = ChatMessage.objects.create(
            session=self.session, role=ChatMessage.Role.ASSISTANT, content="answer"
        )

    def test_web_citation_serializes_without_a_chunk(self):
        citation = MessageCitation.objects.create(
            message=self.message,
            chunk=None,
            source_url="https://example.com",
            source_title="Example Page",
            rank=1,
            score=None,
        )

        data = MessageCitationSerializer(citation).data

        self.assertIsNone(data["chunk_id"])
        self.assertIsNone(data["document_id"])
        self.assertEqual(data["document_title"], "Example Page")
        self.assertIsNone(data["page_number"])
        self.assertEqual(data["section_heading"], "")
        self.assertEqual(data["content"], "")
        self.assertEqual(data["source_url"], "https://example.com")

    def test_chunk_citation_still_serializes_as_before(self):
        chunk = make_chunk("some content")
        citation = MessageCitation.objects.create(message=self.message, chunk=chunk, rank=1, score=0.87)

        data = MessageCitationSerializer(citation).data

        self.assertEqual(data["chunk_id"], chunk.id)
        self.assertEqual(data["document_id"], chunk.document_id)
        self.assertEqual(data["document_title"], chunk.document.title)
        self.assertEqual(data["content"], "some content")
        self.assertEqual(data["score"], 0.87)


class IsSummaryRequestTests(SimpleTestCase):
    def test_recognizes_common_summarization_phrasings(self):
        for question in [
            "Summarize this document",
            "Can you summarise the file?",
            "give me a summary",
            "give me an overview of this",
            "What is this document about?",
            "what's this file about",
            "Explain this document",
            "explain the file to me",
        ]:
            self.assertTrue(_is_summary_request(question), f"expected summary intent for {question!r}")

    def test_does_not_misfire_on_ordinary_factual_questions(self):
        for question in [
            "What is this candidate's work experience?",
            "Where did they study?",
            "What voltage range does the sensor expect?",
            "How many vacation days do employees get?",
        ]:
            self.assertFalse(_is_summary_request(question), f"did not expect summary intent for {question!r}")


class SummaryRequestIntegrationTests(TestCase):
    """Verifies stream_chat_turn() actually routes a summary-intent question
    through get_all_chunks() + SUMMARY_PROMPT instead of retrieve() +
    ANSWER_PROMPT -- the two code paths chat.services chooses between.
    """

    def setUp(self):
        self.session = ChatSession.objects.create()

    @patch("chat.services.get_installed_models", return_value={"llama3.1:8b"})
    @patch("chat.services.condense_question", side_effect=lambda llm, q, h: q)
    @patch("chat.services.get_llm")
    def test_summary_request_uses_all_chunks_not_relevance_retrieval(self, mock_get_llm, mock_condense, mock_installed):
        make_chunk("first part of the document")
        make_chunk("second part of the document")

        with patch("chat.services.retrieve") as mock_retrieve, patch(
            "chat.services.get_all_chunks", wraps=real_get_all_chunks
        ) as mock_get_all, patch("chat.services.stream_answer", return_value=iter(["a summary"])) as mock_stream_answer:
            events = list(stream_chat_turn(self.session, "Please summarize this document", "llama3.1:8b"))

        mock_retrieve.assert_not_called()
        mock_get_all.assert_called_once()
        # stream_answer must have been called with SUMMARY_PROMPT, not the
        # default ANSWER_PROMPT.
        from rag.generation import SUMMARY_PROMPT

        self.assertEqual(mock_stream_answer.call_args.kwargs.get("prompt"), SUMMARY_PROMPT)

        done_event = next(e for e in events if e["event"] == "done")
        self.assertEqual(len(done_event["data"]["citations"]), 2)
