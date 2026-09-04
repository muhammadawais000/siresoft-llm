"""Document upload, status, delete, and re-index endpoints.

Uploads never run ingestion inline — every successful create dispatches
`ingest_document.delay(...)` and returns immediately with the Document's
`queued` status, per the "don't block the request on a 200-page PDF"
requirement. Progress is polled via GET /api/documents/{id}/.
"""

import logging

from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from core.exceptions import FileTooLargeError, UnsupportedFileTypeError
from documents.models import Document
from documents.serializers import DocumentSerializer
from documents.tasks import ingest_document
from documents.validators import (
    compute_file_sha256,
    compute_url_sha256,
    validate_and_resolve_source_type,
    validate_upload_size,
)

logger = logging.getLogger(__name__)


def _uploader(request):
    return request.user if request.user.is_authenticated else None


class DocumentUploadFilesView(APIView):
    """POST /api/documents/upload/files/  — multipart batch file upload.

    Accepts one or more files under the `files` field. Each file is
    validated, hashed, and either linked to an existing Document (if its
    SHA-256 already exists) or queued for ingestion. Partial failures
    within a batch don't fail the whole request — each item reports its
    own outcome.
    """

    parser_classes = [MultiPartParser]

    def post(self, request, *args, **kwargs):
        files = request.FILES.getlist("files")
        if not files:
            return Response(
                {"detail": "No files provided under the 'files' field."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        for f in files:
            results.append(self._handle_one(request, f))
        return Response(results, status=status.HTTP_201_CREATED)

    def _handle_one(self, request, f):
        try:
            source_type = validate_and_resolve_source_type(f.name)
            validate_upload_size(f)
            sha256 = compute_file_sha256(f)
        except (UnsupportedFileTypeError, FileTooLargeError) as exc:
            return {"filename": f.name, "duplicate": False, "error": str(exc), "document": None}

        existing = Document.objects.filter(sha256=sha256).first()
        if existing:
            return {
                "filename": f.name,
                "duplicate": True,
                "error": None,
                "document": DocumentSerializer(existing).data,
            }

        document = Document.objects.create(
            title=f.name,
            source_type=source_type,
            file=f,
            file_size_bytes=f.size,
            sha256=sha256,
            status=Document.Status.QUEUED,
            uploaded_by=_uploader(request),
        )
        ingest_document.delay(document.id)
        logger.info("Queued ingestion for document %s (%s)", document.id, document.title)

        return {
            "filename": f.name,
            "duplicate": False,
            "error": None,
            "document": DocumentSerializer(document).data,
        }


class DocumentUploadURLView(APIView):
    """POST /api/documents/upload/url/  — body: {"urls": ["https://..."]}
    (also accepts a single {"url": "..."} for convenience).
    """

    def post(self, request, *args, **kwargs):
        urls = request.data.get("urls")
        if not urls:
            single = request.data.get("url")
            urls = [single] if single else []

        if not urls:
            return Response(
                {"detail": "Provide at least one URL under 'urls' or 'url'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = [self._handle_one(request, url) for url in urls]
        return Response(results, status=status.HTTP_201_CREATED)

    def _handle_one(self, request, url):
        sha256 = compute_url_sha256(url)
        existing = Document.objects.filter(sha256=sha256).first()
        if existing:
            return {
                "filename": url,
                "duplicate": True,
                "error": None,
                "document": DocumentSerializer(existing).data,
            }

        document = Document.objects.create(
            title=url,
            source_type=Document.SourceType.URL,
            source_url=url,
            sha256=sha256,
            status=Document.Status.QUEUED,
            uploaded_by=_uploader(request),
        )
        ingest_document.delay(document.id)
        logger.info("Queued ingestion for document %s (%s)", document.id, document.title)

        return {
            "filename": url,
            "duplicate": False,
            "error": None,
            "document": DocumentSerializer(document).data,
        }


class DocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    # The document library sidebar renders (and client-side filters) the
    # whole list at once rather than paging through it -- standard REST
    # pagination would silently hide anything past page 1.
    pagination_class = None

    def perform_destroy(self, instance):
        if instance.file:
            instance.file.delete(save=False)
        # Chunk rows (Postgres cascade) and their Qdrant vectors
        # (documents.signals.delete_document_vectors, a pre_delete signal)
        # are both cleaned up as a side effect of this single call.
        instance.delete()

    @action(detail=True, methods=["post"])
    def reindex(self, request, pk=None):
        document = self.get_object()
        document.status = Document.Status.QUEUED
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "updated_at"])
        ingest_document.delay(document.id)
        logger.info("Re-queued ingestion for document %s (%s)", document.id, document.title)
        return Response(DocumentSerializer(document).data)

    @action(detail=True, methods=["get"])
    def chunks(self, request, pk=None):
        """Every chunk this document was split into, in order -- for the
        sidebar's "view chunks" panel. Not a retrieval/relevance endpoint.
        """
        from rag.serializers import ChunkSerializer

        document = self.get_object()
        chunks = document.chunks.order_by("chunk_index")
        return Response(ChunkSerializer(chunks, many=True).data)
