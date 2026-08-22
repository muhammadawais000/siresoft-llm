from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source_type",
        "status",
        "chunk_count",
        "embedding_model_name",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "source_type", "embedding_model_name")
    search_fields = ("title", "sha256", "source_url")
    readonly_fields = (
        "sha256",
        "file_size_bytes",
        "page_count",
        "chunk_count",
        "embedding_model_name",
        "indexed_at",
        "created_at",
        "updated_at",
    )
    actions = ["reindex_selected"]

    @admin.action(description="Re-run ingestion for selected documents")
    def reindex_selected(self, request, queryset):
        from documents.tasks import ingest_document

        count = 0
        for document in queryset:
            document.status = Document.Status.QUEUED
            document.error_message = ""
            document.save(update_fields=["status", "error_message", "updated_at"])
            ingest_document.delay(document.id)
            count += 1
        self.message_user(request, f"Re-queued ingestion for {count} document(s).")
