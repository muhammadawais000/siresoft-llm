from rest_framework import serializers

from .models import Document


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "source_type",
            "source_url",
            "status",
            "error_message",
            "embedding_model_name",
            "page_count",
            "chunk_count",
            "file_size_bytes",
            "sha256",
            "created_at",
            "updated_at",
            "indexed_at",
        ]
        read_only_fields = fields


class DocumentUploadResultSerializer(serializers.Serializer):
    """Per-file/per-URL outcome of a batch upload — success, duplicate, or
    a validation error. Kept separate from DocumentSerializer since a
    failed item never becomes a Document row.
    """

    filename = serializers.CharField()
    duplicate = serializers.BooleanField(default=False)
    error = serializers.CharField(required=False, allow_null=True)
    document = DocumentSerializer(required=False, allow_null=True)
