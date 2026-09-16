"""
apps/messaging/services.py
────────────────────────────
Conversation resolution: find-or-create a Conversation by matching the exact
normalized participant set + DID, scoped to tenant. Used by both the outbound
send flow and the inbound Telnyx webhook so repeat sends/receives to the same
group land in the same thread.

Also handles downloading MMS media (inbound and outbound) into local storage
so it's served through this API's own auth rather than an unauthenticated
third-party URL — see apps.messaging.models.MessageMedia.
"""

import json
import logging
import os
import re
import uuid

import httpx
from django.conf import settings
from django.utils import timezone

from apps.contacts.services import build_contact_lookup
from apps.dids.models import UserDID
from apps.messaging.models import Conversation, ConversationParticipant, MessageMedia
from apps.outbox.models import OutboxEvent, OutboxTargetType

logger = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r"\D+")
_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 30.0


def normalize_number(raw_number: str) -> str:
    """Normalizes to a bare-digits E.164-ish form for participant-set matching."""
    digits = _DIGITS_RE.sub("", str(raw_number or ""))
    if digits and not str(raw_number).strip().startswith("+"):
        return digits
    return digits


def resolve_conversation(tenant, did, participant_numbers: list) -> Conversation:
    """
    Finds a Conversation on this tenant/DID whose participant set exactly
    matches `participant_numbers`, or creates one (with participants,
    resolved against Contacts) if none exists.
    """
    normalized = sorted({normalize_number(n) for n in participant_numbers if n})
    if not normalized:
        raise ValueError("At least one participant number is required.")

    candidates = Conversation.objects.filter(tenant=tenant, did=did).prefetch_related("participants")
    for convo in candidates:
        existing = sorted({normalize_number(p.phone_number) for p in convo.participants.all()})
        if existing == normalized:
            return convo

    convo = Conversation.objects.create(
        tenant=tenant,
        did=did,
        is_group=len(normalized) > 1,
        last_message_at=timezone.now(),
    )

    lookup = build_contact_lookup(tenant, participant_numbers)
    ConversationParticipant.objects.bulk_create([
        ConversationParticipant(
            conversation=convo,
            phone_number=number,
            contact_id=lookup.get(number, {}).get("contact_id"),
        )
        for number in dict.fromkeys(participant_numbers)
        if number
    ])

    return convo


def download_message_media(message, media_urls: list) -> list:
    """
    Downloads each URL in `media_urls` (Telnyx-hosted for inbound, or
    whatever the sender supplied for outbound) into MESSAGING_MEDIA_ROOT and
    creates a MessageMedia row for it, so it's served back through this
    API's own JWT-gated endpoint instead of the original URL.

    Runs synchronously in the request path (both the outbound send view and
    the inbound webhook handler) — MMS attachments are a handful of images
    per message, not bulk media, so this is a small, bounded delay rather
    than something that needs a background task queue.

    A single URL's download failure is logged and skipped rather than
    aborting the whole message — the message itself (and any other
    attachments) must still be saved even if one attachment couldn't be
    fetched.
    """
    if not media_urls:
        return []

    os.makedirs(settings.MESSAGING_MEDIA_ROOT, exist_ok=True)

    created = []
    with httpx.Client(timeout=_MEDIA_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for url in media_urls:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as err:
                logger.error("Failed to download message media %s for message %s: %s", url, message.id, err)
                continue

            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            ext = _extension_for_content_type(content_type, url)
            filename = f"{uuid.uuid4()}{ext}"
            abs_path = os.path.join(settings.MESSAGING_MEDIA_ROOT, filename)

            with open(abs_path, "wb") as f:
                f.write(resp.content)

            created.append(
                MessageMedia.objects.create(
                    message=message,
                    source_url=url,
                    file_path=filename,
                    content_type=content_type,
                    size_bytes=len(resp.content),
                )
            )

    return created


def _extension_for_content_type(content_type: str, url: str) -> str:
    """Best-effort file extension, preferring the response Content-Type over the URL (Telnyx media URLs carry no filename/extension)."""
    common = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp",
        "video/mp4": ".mp4", "video/quicktime": ".mov", "video/3gpp": ".3gp",
        "audio/mpeg": ".mp3", "audio/amr": ".amr", "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
    }
    if content_type in common:
        return common[content_type]
    url_ext = os.path.splitext(url.split("?")[0])[1]
    return url_ext if len(url_ext) <= 5 else ""


def broadcast_message_event(message) -> None:
    """
    Emits two OutboxEvents for a newly created/updated Message, both
    delivered via the existing outbox → Celery → Channels pipeline
    (apps.outbox.tasks.dispatch_pending_outbox_events, ~2s poll):

    1. target_type="conversation" — one event, to everyone with that
       conversation's thread open (apps.messaging.consumers.ConversationConsumer,
       group "conversation.{id}"). Lets an open chat window update live.

    2. target_type="user" — one event per user the message's DID is
       assigned to (UserDID), delivered to their personal feed
       (apps.outbox.consumers.UserEventsConsumer, group "user.{id}").
       Lets an inbox/badge update live even when that conversation isn't
       open, without needing a not-yet-wired-up tenant-wide broadcast.

    Uses the message's already-serialized data (MessageSerializer) as the
    payload so both feeds carry the exact same shape the REST API returns —
    the frontend can render either without a special WebSocket-only schema.
    """
    # Imported here, not at module load, to avoid a circular import
    # (serializers.py -> models.py, and this module is imported by views.py
    # which also imports serializers.py — importing serializers at module
    # scope here would risk a cycle depending on import order).
    from apps.messaging.serializers import MessageSerializer

    payload = MessageSerializer(message).data
    # MessageSerializer's UUID/datetime fields aren't JSON-native; DRF's
    # Response rendering handles that for REST, but OutboxEvent.payload is a
    # plain JSONField written directly, so it must already be JSON-safe.
    payload = json.loads(json.dumps(payload, default=str))

    OutboxEvent.objects.create(
        tenant=message.tenant,
        target_type=OutboxTargetType.CONVERSATION,
        target_id=str(message.conversation_id),
        event_type="message.created",
        payload=payload,
    )

    user_ids = UserDID.objects.filter(did_id=message.did_id).values_list("user_id", flat=True)
    for user_id in set(user_ids):
        OutboxEvent.objects.create(
            tenant=message.tenant,
            target_type=OutboxTargetType.USER,
            target_id=str(user_id),
            event_type="message.created",
            payload=payload,
        )
