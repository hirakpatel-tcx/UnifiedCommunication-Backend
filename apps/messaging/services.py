"""
apps/messaging/services.py
────────────────────────────
Conversation resolution: find-or-create a Conversation by matching the exact
normalized participant set + DID, scoped to tenant. Used by both the outbound
send flow and the inbound Telnyx webhook so repeat sends/receives to the same
group land in the same thread.
"""

import re

from django.utils import timezone

from apps.contacts.services import build_contact_lookup
from apps.messaging.models import Conversation, ConversationParticipant

_DIGITS_RE = re.compile(r"\D+")


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
