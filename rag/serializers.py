from rest_framework import serializers

from .models import Chunk


class ChunkSerializer(serializers.ModelSerializer):
    """Every chunk a document was split into, independent of any query --
    used by the document library's "view chunks" panel, not retrieval.
    """

    class Meta:
        model = Chunk
        fields = ["id", "chunk_index", "page_number", "section_heading", "content", "token_count"]
        read_only_fields = fields


class RetrievalRequestSerializer(serializers.Serializer):
    query = serializers.CharField(allow_blank=False)
    top_k = serializers.IntegerField(required=False, min_value=1, max_value=50)
    candidate_pool = serializers.IntegerField(required=False, min_value=1, max_value=200)
    similarity_threshold = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    document_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )


class RetrievedChunkSerializer(serializers.Serializer):
    chunk_id = serializers.IntegerField()
    document_id = serializers.IntegerField()
    document_title = serializers.CharField()
    page_number = serializers.IntegerField(allow_null=True)
    section_heading = serializers.CharField(allow_blank=True)
    content = serializers.CharField()
    score = serializers.FloatField()
    dense_rank = serializers.IntegerField(allow_null=True)
    sparse_rank = serializers.IntegerField(allow_null=True)
