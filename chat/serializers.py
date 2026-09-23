from rest_framework import serializers

from .models import ChatMessage, ChatSession, MessageCitation


class MessageCitationSerializer(serializers.ModelSerializer):
    # Field names here intentionally match RetrievedChunk.to_citation()'s
    # (and WebResult.to_citation()'s) SSE payload shape, so the frontend
    # renders a citation the same way whether it just streamed in or was
    # loaded from chat history -- and the same way regardless of whether
    # it's a document chunk or a web result (chunk is null for the
    # latter, see chat.models.MessageCitation).
    chunk_id = serializers.IntegerField(read_only=True)
    document_id = serializers.SerializerMethodField()
    document_title = serializers.SerializerMethodField()
    page_number = serializers.SerializerMethodField()
    section_heading = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()

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
            "source_url",
        ]
        read_only_fields = fields

    def get_document_id(self, obj):
        return obj.chunk.document_id if obj.chunk_id else None

    def get_document_title(self, obj):
        return obj.chunk.document.title if obj.chunk_id else obj.source_title

    def get_page_number(self, obj):
        return obj.chunk.page_number if obj.chunk_id else None

    def get_section_heading(self, obj):
        return obj.chunk.section_heading if obj.chunk_id else ""

    def get_content(self, obj):
        return obj.chunk.content if obj.chunk_id else ""


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
