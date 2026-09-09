"""
apps/messaging/views.py
─────────────────────────
Messaging REST API: conversation list, thread history, and outbound send.

Enforces:
- 'messaging' tenant feature flag.
- DID ownership (the DID must belong to the caller's tenant).
- Single platform-wide Telnyx credential, tenant distinguished by
  Tenant.telnyx_messaging_profile_id.
"""

import logging

from django.utils import timezone
from httpx import HTTPStatusError, RequestError
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.telnyx_client import TelnyxClientService
from apps.common.tenant_resolver import get_scoped_tenant
from apps.dids.models import DID
from apps.messaging.models import Conversation, Message, MessageDirection, MessageStatus
from apps.messaging.serializers import ConversationSerializer, MessageSerializer, SendMessageSerializer
from apps.messaging.services import resolve_conversation

logger = logging.getLogger(__name__)


def _validate_messaging_feature(tenant):
    if not tenant.messaging_enabled:
        return Response(
            {"detail": "Messaging feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


class ConversationListView(generics.ListAPIView):
    """GET /api/v1/messaging/conversations/"""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        return (
            Conversation.objects.filter(tenant=tenant)
            .prefetch_related("participants", "messages")
            .order_by("-last_message_at", "-created_at")
        )


class ConversationMessagesView(generics.ListAPIView):
    """GET /api/v1/messaging/conversations/{id}/messages/"""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        conversation_id = self.kwargs["conversation_id"]
        return Message.objects.filter(
            tenant=tenant, conversation_id=conversation_id
        ).order_by("created_at")


class SendMessageView(APIView):
    """POST /api/v1/messaging/messages/send/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        tenant = get_scoped_tenant(request)

        feature_error = _validate_messaging_feature(tenant)
        if feature_error:
            return feature_error

        if not tenant.telnyx_messaging_profile_id:
            return Response(
                {"detail": "Tenant has no Telnyx Messaging Profile configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        did = data["did"]
        if did.tenant_id != tenant.id:
            return Response(
                {"detail": "DID does not belong to this tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        to_numbers = data["to_numbers"]
        body = data.get("body", "")
        media_urls = data.get("media_urls") or []

        conversation = resolve_conversation(tenant, did, to_numbers)

        message = Message.objects.create(
            conversation=conversation,
            tenant=tenant,
            did=did,
            user=request.user,
            direction=MessageDirection.OUTBOUND,
            from_number=did.number,
            body=body,
            media_urls=media_urls,
            status=MessageStatus.QUEUED,
        )

        try:
            telnyx_response = TelnyxClientService.send_message(
                messaging_profile_id=tenant.telnyx_messaging_profile_id,
                from_number=did.number,
                to_numbers=to_numbers,
                text=body,
                media_urls=media_urls or None,
            )
        except HTTPStatusError as err:
            message.status = MessageStatus.FAILED
            message.error_code = str(err.response.status_code)
            message.error_detail = err.response.text
            message.save(update_fields=["status", "error_code", "error_detail", "updated_at"])
            logger.error("Telnyx send failed for message %s: %s", message.id, err)
            return Response(
                {"detail": "Telnyx rejected the message.", "error": err.response.text},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except RequestError as err:
            message.status = MessageStatus.FAILED
            message.error_detail = str(err)
            message.save(update_fields=["status", "error_detail", "updated_at"])
            logger.error("Telnyx send network error for message %s: %s", message.id, err)
            return Response(
                {"detail": "Unable to reach Telnyx."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        telnyx_data = telnyx_response.get("data", {})
        message.telnyx_message_id = telnyx_data.get("id")
        message.status = MessageStatus.SENT
        message.sent_at = timezone.now()
        message.save(update_fields=["telnyx_message_id", "status", "sent_at", "updated_at"])

        conversation.last_message_at = message.sent_at
        conversation.save(update_fields=["last_message_at", "updated_at"])

        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)
