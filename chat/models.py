from django.conf import settings
from django.db import models

from core.models import TimeStampedModel
from rag.models import Chunk


class ChatSession(TimeStampedModel):
    """A conversation thread. Message history within a session is what
    feeds history-aware query rewriting for follow-up questions.
    """

    title = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_sessions",
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Session {self.pk}"


class ChatMessage(TimeStampedModel):
    """One turn in a session. `model_used` and `latency_ms` are recorded on
    every assistant message so answer quality/speed can be compared across
    the five selectable Ollama models after the fact.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()

    # Only meaningful for assistant messages.
    model_used = models.CharField(max_length=128, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)

    # The standalone query actually sent to the retriever, after
    # history-aware rewriting. Equal to `content` for the first message in
    # a session; useful for debugging why retrieval returned what it did.
    rewritten_query = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}"


class MessageCitation(models.Model):
    """Links an assistant ChatMessage to the chunks its answer was grounded
    in, with the retrieval/rerank score preserved for the UI's sources
    panel. A through-table (rather than a JSON blob on ChatMessage) so
    citations stay queryable and cascade-delete cleanly with their chunk.
    """

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="citations")
    chunk = models.ForeignKey(Chunk, on_delete=models.CASCADE, related_name="citations")
    rank = models.IntegerField()
    score = models.FloatField()

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "chunk"], name="unique_citation_per_message_chunk"
            ),
        ]

    def __str__(self):
        return f"citation #{self.rank} for message {self.message_id}"
