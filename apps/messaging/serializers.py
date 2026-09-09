from rest_framework import serializers

from apps.dids.models import DID
from apps.messaging.models import Conversation, ConversationParticipant, Message


class ConversationParticipantSerializer(serializers.ModelSerializer):
    contact_saved = serializers.SerializerMethodField()

    class Meta:
        model = ConversationParticipant
        fields = ["id", "phone_number", "contact", "contact_saved"]

    def get_contact_saved(self, obj):
        return obj.contact_id is not None


class MessageSerializer(serializers.ModelSerializer):
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

    class Meta:
        model = Conversation
        fields = [
            "id", "did", "is_group", "subject", "last_message_at",
            "participants", "last_message", "created_at",
        ]

    def get_last_message(self, obj):
        last = obj.messages.order_by("-created_at").first()
        return MessageSerializer(last).data if last else None


class SendMessageSerializer(serializers.Serializer):
    did = serializers.PrimaryKeyRelatedField(queryset=DID.objects.all())
    to_numbers = serializers.ListField(
        child=serializers.CharField(max_length=20), allow_empty=False
    )
    body = serializers.CharField(required=False, allow_blank=True, default="")
    media_urls = serializers.ListField(
        child=serializers.URLField(), required=False, default=list
    )
