from rest_framework import serializers

from .models import ChatMessage, ChatSession, MessageCitation


class MessageCitationSerializer(serializers.ModelSerializer):
    # Field names here intentionally match RetrievedChunk.to_citation()'s
    # SSE payload shape, so the frontend can render a citation the same way
    # whether it just streamed in or was loaded from chat history.
    chunk_id = serializers.IntegerField(source="chunk_id", read_only=True)
    document_id = serializers.IntegerField(source="chunk.document_id", read_only=True)
    document_title = serializers.CharField(source="chunk.document.title", read_only=True)
    page_number = serializers.IntegerField(source="chunk.page_number", read_only=True)
    section_heading = serializers.CharField(source="chunk.section_heading", read_only=True)
    content = serializers.CharField(source="chunk.content", read_only=True)

    class Meta:
        model = MessageCitation
        fields = [
            "chunk_id",
            "rank",
            "score",
            "document_id",
            "document_title",
            "page_number",
            "section_heading",
            "content",
        ]
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    citations = MessageCitationSerializer(many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            "id",
            "role",
            "content",
            "model_used",
            "latency_ms",
            "rewritten_query",
            "created_at",
            "citations",
        ]
        read_only_fields = fields


class ChatSessionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ["id", "title", "created_at", "updated_at"]
        read_only_fields = fields


class ChatSessionDetailSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = ChatSession
        fields = ["id", "title", "created_at", "updated_at", "messages"]
        read_only_fields = fields
