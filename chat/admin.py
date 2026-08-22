from django.contrib import admin

from .models import ChatMessage, ChatSession, MessageCitation


class MessageCitationInline(admin.TabularInline):
    model = MessageCitation
    extra = 0
    readonly_fields = ("chunk", "rank", "score")
    can_delete = False


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ("role", "content", "model_used", "latency_ms", "created_at")
    readonly_fields = ("created_at",)


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "created_at", "updated_at")
    search_fields = ("title",)
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "model_used", "latency_ms", "created_at")
    list_filter = ("role", "model_used")
    search_fields = ("content",)
    inlines = [MessageCitationInline]
