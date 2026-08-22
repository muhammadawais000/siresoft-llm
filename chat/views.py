"""Chat session/message CRUD (DRF) plus the SSE streaming endpoint.

The streaming endpoint is a plain Django View, not a DRF APIView — DRF's
response/content-negotiation machinery expects a `rest_framework.Response`
back from the handler, which doesn't fit a raw `StreamingHttpResponse`.
Server-Sent Events over a plain Django view (rather than WebSockets/Channels)
is also the simpler choice given the project's Gunicorn+nginx deployment
target: no extra ASGI server needed, just an SSE-aware nginx proxy config.
"""

import json
import logging

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import mixins, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from core.exceptions import OllamaUnavailableError
from core.ollama import get_installed_models

from .models import ChatSession
from .serializers import ChatSessionDetailSerializer, ChatSessionListSerializer
from .services import stream_chat_turn

logger = logging.getLogger(__name__)


class ChatSessionViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    queryset = ChatSession.objects.all()
    # The history popover renders the whole recent-sessions list at once.
    pagination_class = None

    def get_serializer_class(self):
        if self.action == "list":
            return ChatSessionListSerializer
        return ChatSessionDetailSerializer


class ModelListView(APIView):
    """GET /api/chat/models/ — the 5 configured models, flagged by whether
    they're actually pulled on the Ollama server right now. The frontend
    disables anything with installed=false rather than letting a user pick
    a model that will 404 mid-request.
    """

    def get(self, request, *args, **kwargs):
        installed = get_installed_models()
        return Response(
            [
                {"name": name, "installed": name in installed, "is_default": name == settings.OLLAMA_DEFAULT_MODEL}
                for name in settings.OLLAMA_MODELS
            ]
        )


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@method_decorator(csrf_exempt, name="dispatch")
class SendMessageView(View):
    """POST /api/chat/sessions/{id}/messages/  body: {"question", "model", "document_ids"?}

    Streams the assistant's reply as Server-Sent Events:
      event: query_rewrite  -> {"rewritten_query": "..."}
      event: token           -> "<token text>"
      event: done            -> {"message_id": ..., "citations": [...]}
      event: error           -> {"detail": "..."}
    """

    def post(self, request, session_id, *args, **kwargs):
        session = get_object_or_404(ChatSession, pk=session_id)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "Invalid JSON body."}, status=status.HTTP_400_BAD_REQUEST)

        question = (body.get("question") or "").strip()
        model_name = body.get("model") or settings.OLLAMA_DEFAULT_MODEL
        document_ids = body.get("document_ids") or None

        if not question:
            return JsonResponse({"detail": "'question' is required."}, status=status.HTTP_400_BAD_REQUEST)
        if model_name not in settings.OLLAMA_MODELS:
            return JsonResponse(
                {"detail": f"'{model_name}' is not one of the configured models."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def event_stream():
            try:
                for event in stream_chat_turn(session, question, model_name, document_ids=document_ids):
                    yield _sse(event["event"], event["data"])
            except OllamaUnavailableError as exc:
                logger.warning("Ollama unavailable during chat turn (session=%s): %s", session.id, exc)
                yield _sse("error", {"detail": str(exc)})
            except Exception:
                logger.exception("Unhandled error streaming chat turn (session=%s)", session.id)
                yield _sse("error", {"detail": "An unexpected error occurred while generating the response."})

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # tell nginx not to buffer the stream
        return response
