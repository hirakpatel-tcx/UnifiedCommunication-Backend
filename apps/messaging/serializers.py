from rest_framework import serializers

from apps.dids.models import DID
from apps.messaging.models import Conversation, ConversationParticipant, Message, MessageMedia


class ConversationParticipantSerializer(serializers.ModelSerializer):
    contact_saved = serializers.SerializerMethodField()

    class Meta:
        model = ConversationParticipant
        fields = ["id", "phone_number", "contact", "contact_saved"]

    def get_contact_saved(self, obj):
        return obj.contact_id is not None


class MessageMediaSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = MessageMedia
        fields = ["id", "url", "content_type", "size_bytes"]

    def get_url(self, obj):
        return f"/api/v1/messaging/media/{obj.id}/"


class MessageSerializer(serializers.ModelSerializer):
    media_urls = MessageMediaSerializer(source="media_files", many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            "id", "conversation", "tenant", "did", "user", "direction",
            "from_number", "body", "media_urls", "telnyx_message_id",
            "status", "error_code", "error_detail", "sent_at", "delivered_at",
            "created_at",
        ]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    participants = ConversationParticipantSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "did", "is_group", "subject", "last_message_at",
            "participants", "last_message", "unread", "created_at",
        ]

    def get_last_message(self, obj):
        last = obj.messages.order_by("-created_at").first()
        return MessageSerializer(last).data if last else None

    def get_unread(self, obj) -> bool:
        """
        True when this conversation has activity (last_message_at) more
        recent than the requesting user's own read position — i.e. per
        viewer, not a single global flag. Requires "request" in the
        serializer context (see ConversationListView.get_serializer_context);
        without it, always returns False rather than raising.
        """
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or not obj.last_message_at:
            return False
        read_state = next(
            (r for r in obj.read_states.all() if r.user_id == request.user.id), None
        )
        if read_state is None:
            return True
        return read_state.last_read_at < obj.last_message_at


class SendMessageSerializer(serializers.Serializer):
    did = serializers.PrimaryKeyRelatedField(queryset=DID.objects.all())
    to_numbers = serializers.ListField(
        child=serializers.CharField(max_length=20), allow_empty=False
    )
    body = serializers.CharField(required=False, allow_blank=True, default="")
    media_urls = serializers.ListField(
        child=serializers.URLField(), required=False, default=list
    )
