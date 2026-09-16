"""
apps/messaging/models.py
─────────────────────────
SMS/MMS messaging via Telnyx.

Unlike CDR and voicemail, Telnyx has no queryable message-history API, so
this app's database is the system of record for message threads (see
docs/plans/telnyx-messaging-plan.md).
"""

from django.db import models

from apps.common.models import TimestampedModel


class Conversation(TimestampedModel):
    """A message thread anchored to one tenant-owned DID, 1:1 or group."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="conversations")
    did = models.ForeignKey("dids.DID", on_delete=models.CASCADE, related_name="conversations")
    is_group = models.BooleanField(default=False)
    subject = models.CharField(max_length=255, blank=True, default="")
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "did"]),
            models.Index(fields=["tenant", "-last_message_at"]),
        ]

    def __str__(self):
        return f"Conversation({self.id}) on {self.did_id}"


class ConversationParticipant(TimestampedModel):
    """An external phone number participating in a Conversation."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    phone_number = models.CharField(max_length=20)
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "phone_number"], name="uniq_conversation_participant"
            )
        ]
        indexes = [
            models.Index(fields=["conversation"]),
        ]

    def __str__(self):
        return f"{self.phone_number} in {self.conversation_id}"


class MessageDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class MessageStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    RECEIVED = "received", "Received"


class Message(TimestampedModel):
    """A single SMS/MMS send or receive within a Conversation."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="messages")
    did = models.ForeignKey("dids.DID", on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_messages"
    )

    direction = models.CharField(max_length=10, choices=MessageDirection.choices)
    from_number = models.CharField(max_length=20)
    body = models.TextField(blank=True, default="")
    # Deprecated: raw Telnyx/sender-supplied media URLs, kept only for
    # messages created before MessageMedia existed. New sends/receives store
    # downloaded copies as MessageMedia rows instead (see media_files) and
    # leave this empty — MessageSerializer.media_urls now derives its output
    # from media_files, not this field.
    media_urls = models.JSONField(default=list, blank=True)

    telnyx_message_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    status = models.CharField(max_length=12, choices=MessageStatus.choices, default=MessageStatus.QUEUED)
    error_code = models.CharField(max_length=32, null=True, blank=True)
    error_detail = models.TextField(null=True, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["tenant", "did"]),
        ]

    def __str__(self):
        return f"Message({self.id}) {self.direction} on {self.conversation_id}"


class ConversationRead(TimestampedModel):
    """
    Tracks how far a specific user has read into a Conversation, so
    unread/read state can be computed per-viewer rather than globally —
    multiple admins/users on the same tenant may open the same conversation
    independently, and each should see their own read position.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="read_states")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="conversation_read_states")
    last_read_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conversation", "user"], name="uniq_conversation_read_user"),
        ]
        indexes = [
            models.Index(fields=["user", "conversation"]),
        ]

    def __str__(self):
        return f"ConversationRead(user={self.user_id}, conversation={self.conversation_id})"


class MessageMedia(TimestampedModel):
    """
    One MMS attachment belonging to a Message, downloaded into local storage
    at send/receive time rather than served from Telnyx's (inbound) or the
    sender's (outbound) original URL directly — so access is gated by this
    API's own JWT auth instead of an unauthenticated third-party link.
    """

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="media_files")
    source_url = models.URLField(max_length=2000, help_text="The original Telnyx/sender-supplied URL this was downloaded from.")
    file_path = models.CharField(max_length=512, help_text="Path on disk, relative to MESSAGING_MEDIA_ROOT.")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["message"]),
        ]

    def __str__(self):
        return f"MessageMedia({self.id}) on {self.message_id}"
