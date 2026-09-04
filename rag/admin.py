from django.contrib import admin

from .models import Chunk


@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "page_number", "section_heading", "token_count")
    list_filter = ("document",)
    search_fields = ("content", "section_heading")
    readonly_fields = ("document", "content_tsv", "ingested_at")

    def has_add_permission(self, request):
        # Chunks are only ever created by the ingestion pipeline.
        return False
