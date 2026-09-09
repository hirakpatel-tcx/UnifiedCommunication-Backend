from django.contrib import admin

from apps.messaging.models import Conversation, ConversationParticipant, Message


class ConversationParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 0


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "did", "is_group", "last_message_at", "created_at")
    list_filter = ("tenant", "is_group")
    inlines = [ConversationParticipantInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id", "conversation", "tenant", "direction", "status",
        "from_number", "telnyx_message_id", "created_at",
    )
    list_filter = ("tenant", "direction", "status")
    search_fields = ("telnyx_message_id", "from_number", "body")
