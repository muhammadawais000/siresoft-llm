"""Retrieval debug/search endpoint.

Exists both as an operator debugging tool (inspect what a query would
retrieve without going through the LLM) and as the retrieval leg the
chat generation chain calls into in Phase 4.
"""

from rest_framework.response import Response
from rest_framework.views import APIView

from rag.retrieval import retrieve
from rag.serializers import RetrievalRequestSerializer, RetrievedChunkSerializer


class RetrievalSearchView(APIView):
    """POST /api/rag/search/ — run hybrid retrieval + rerank directly."""

    def post(self, request, *args, **kwargs):
        params = RetrievalRequestSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        data = params.validated_data

        results = retrieve(
            data["query"],
            top_k=data.get("top_k"),
            candidate_pool=data.get("candidate_pool"),
            similarity_threshold=data.get("similarity_threshold"),
            document_ids=data.get("document_ids"),
        )

        payload = [
            {
                "chunk_id": r.chunk.id,
                "document_id": r.chunk.document_id,
                "document_title": r.chunk.document.title,
                "page_number": r.chunk.page_number,
                "section_heading": r.chunk.section_heading,
                "content": r.chunk.content,
                "score": r.score,
                "dense_rank": r.dense_rank,
                "sparse_rank": r.sparse_rank,
            }
            for r in results
        ]
        return Response(RetrievedChunkSerializer(payload, many=True).data)
