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
import os

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from httpx import HTTPStatusError, RequestError
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Q

from apps.common.services.telnyx_client import TelnyxClientService
from apps.common.tenant_resolver import get_scoped_tenant
from apps.dids.models import DID
from apps.messaging.models import Conversation, ConversationRead, Message, MessageDirection, MessageMedia, MessageStatus
from apps.messaging.serializers import ConversationSerializer, MessageSerializer, SendMessageSerializer
from apps.messaging.services import broadcast_message_event, download_message_media, resolve_conversation

logger = logging.getLogger(__name__)


def _validate_messaging_feature(tenant):
    if not tenant.messaging_enabled:
        return Response(
            {"detail": "Messaging feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


class ConversationListView(generics.ListAPIView):
    """
    GET /api/v1/messaging/conversations/
    Params:
      search   — matches a participant's phone_number (contains) or a saved
                 Contact's name, case-insensitive.
      did_id   — restrict to conversations on this DID.
      start, end — ISO datetimes; restrict to conversations with
                 last_message_at in this range.
      unread   — "true" to return only conversations with unread messages
                 for the requesting user (see ConversationSerializer.unread_count).
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        qs = (
            Conversation.objects.filter(tenant=tenant)
            .prefetch_related("participants__contact", "messages", "read_states")
            .order_by("-last_message_at", "-created_at")
        )

        params = self.request.query_params

        search = params.get("search")
        if search:
            qs = qs.filter(
                Q(participants__phone_number__icontains=search)
                | Q(participants__contact__first_name__icontains=search)
                | Q(participants__contact__last_name__icontains=search)
            ).distinct()

        did_id = params.get("did_id")
        if did_id:
            qs = qs.filter(did_id=did_id)

        start = params.get("start")
        if start:
            qs = qs.filter(last_message_at__gte=start)
        end = params.get("end")
        if end:
            qs = qs.filter(last_message_at__lte=end)

        if params.get("unread", "").lower() == "true":
            # Unread = last_message_at is newer than this user's last_read_at
            # for that conversation (or the user has no read record at all).
            # Done in Python against the already-fetched prefetch (read_states),
            # rather than a second query, since the queryset is typically a
            # single tenant's conversation count (not large enough to need
            # DB-side filtering here).
            user = self.request.user
            unread_ids = [
                c.id for c in qs
                if c.last_message_at and (
                    (rs := next((r for r in c.read_states.all() if r.user_id == user.id), None)) is None
                    or rs.last_read_at < c.last_message_at
                )
            ]
            qs = qs.filter(id__in=unread_ids)

        return qs


class ConversationMessagesView(generics.ListAPIView):
    """
    GET /api/v1/messaging/conversations/{id}/messages/
    Params:
      search — matches message body (contains), case-insensitive.
      start, end — ISO datetimes; restrict to messages created in this range.
      direction — "inbound" or "outbound".
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        conversation_id = self.kwargs["conversation_id"]
        qs = Message.objects.filter(
            tenant=tenant, conversation_id=conversation_id
        ).order_by("created_at")

        params = self.request.query_params

        search = params.get("search")
        if search:
            qs = qs.filter(body__icontains=search)

        start = params.get("start")
        if start:
            qs = qs.filter(created_at__gte=start)
        end = params.get("end")
        if end:
            qs = qs.filter(created_at__lte=end)

        direction = params.get("direction")
        if direction in (MessageDirection.INBOUND, MessageDirection.OUTBOUND):
            qs = qs.filter(direction=direction)

        return qs


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

        # Store our own copy of each attachment (from the sender-supplied
        # URLs, which Telnyx has already been given above) so the frontend
        # reads it back through this API's own auth rather than whatever
        # third-party URL the sender originally provided.
        if media_urls:
            download_message_media(message, media_urls)

        conversation.last_message_at = message.sent_at
        conversation.save(update_fields=["last_message_at", "updated_at"])

        broadcast_message_event(message)

        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)


class MessageMediaView(APIView):
    """
    GET /api/v1/messaging/media/{media_id}/
    Streams a locally-stored MMS attachment (inbound or outbound). Requires
    the same JWT auth as the rest of this API — the file is never served
    from Telnyx's/the sender's original URL, only from our own copy, gated
    by this endpoint checking the owning Message's tenant against the
    caller's scoped tenant.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_id, *args, **kwargs):
        tenant = get_scoped_tenant(request)

        media = MessageMedia.objects.filter(id=media_id, message__tenant=tenant).select_related("message").first()
        if not media:
            raise Http404

        abs_path = os.path.join(settings.MESSAGING_MEDIA_ROOT, media.file_path)
        if not os.path.isfile(abs_path):
            raise Http404

        return FileResponse(
            open(abs_path, "rb"),
            content_type=media.content_type or "application/octet-stream",
        )


class ConversationMarkReadView(APIView):
    """
    POST /api/v1/messaging/conversations/{id}/read/
    Marks the conversation as read (up to now) for the requesting user,
    upserting their ConversationRead row. No body required.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id, *args, **kwargs):
        tenant = get_scoped_tenant(request)
        conversation = Conversation.objects.filter(id=conversation_id, tenant=tenant).first()
        if not conversation:
            raise Http404

        ConversationRead.objects.update_or_create(
            conversation=conversation,
            user=request.user,
            defaults={"last_read_at": timezone.now()},
        )
        return Response({"status": "ok"}, status=status.HTTP_200_OK)
