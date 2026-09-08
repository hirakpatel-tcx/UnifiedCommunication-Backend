"""
apps/outbox/models.py
──────────────────────
OutboxEvent — reliable database-to-WebSocket delivery pattern.

Design decisions:
- The Outbox pattern decouples database writes from WebSocket delivery.
  If a WebSocket publish fails, the OutboxEvent retains 'failed' status
  and can be re-dispatched without re-running the business logic.

- CRITICAL: target_type must ALWAYS be explicit. It is NEVER null.
  "user"   → send only to user.{target_id} channel group
  "tenant" → send to tenant.{target_id} group (explicitly authorized only;
              never the default behavior)

  There is NO "broadcast to all" by leaving target_user null or
  by any other implicit mechanism. Every OutboxEvent must name an
  explicit target.

- OutboxEvent is created WITHIN the same DB transaction that modifies
  the resource. This ensures the event is only dispatched if the
  resource update actually committed.

  DB transaction:
    ├── UPDATE/INSERT resource
    └── INSERT OutboxEvent (status=pending)
    ── transaction commits ──
         ↓
    Celery outbox worker
         ↓
    channel_layer.group_send(...)
         ↓
    OutboxEvent.status = dispatched

- payload is the normalized application event — no raw FreeSWITCH payloads,
  no secrets, no SIP passwords, no API keys.
"""

from django.db import models

from apps.common.models import UUIDModel


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class OutboxEventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DISPATCHED = "dispatched", "Dispatched"
    FAILED = "failed", "Failed"


class OutboxTargetType(models.TextChoices):
    USER = "user", "User"
    TENANT = "tenant", "Tenant"


# ---------------------------------------------------------------------------
# OutboxEvent
# ---------------------------------------------------------------------------

class OutboxEvent(UUIDModel):
    """
    Reliable delivery record: database write → Redis channel layer → WebSocket.

    Created atomically with the resource mutation that triggers the event.
    Picked up by the Celery outbox worker and published to Django Channels.

    target_type + target_id determine the channel group:
      user:   "user.{target_id}"    → personal channel
      tenant: "tenant.{target_id}"  → tenant-wide channel (requires explicit auth)

    Security:
    - payload must contain only normalized, non-sensitive application data.
    - payload must NOT contain: SIP passwords, FreeSWITCH API keys,
      application password hashes, raw webhook payloads.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="outbox_events",
        help_text="The tenant context for this event.",
    )

    # ------------------------------------------------------------------
    # Target — EXPLICIT, MANDATORY
    # ------------------------------------------------------------------
    target_type = models.CharField(
        max_length=20,
        choices=OutboxTargetType.choices,
        help_text=(
            "Explicit delivery target type. NEVER null. "
            "'user'   → channel group: user.{target_id} "
            "'tenant' → channel group: tenant.{target_id} "
            "Tenant-wide events require explicit authorization at dispatch time."
        ),
    )
    target_id = models.CharField(
        max_length=255,
        help_text=(
            "The target identifier. "
            "For target_type='user': the User UUID string. "
            "For target_type='tenant': the FreeSWITCH tenant UUID string."
        ),
    )

    # ------------------------------------------------------------------
    # Event data
    # ------------------------------------------------------------------
    event_type = models.CharField(
        max_length=100,
        help_text=(
            "Normalized application event type "
            "(e.g. 'call.incoming', 'extension.updated', 'fax.received'). "
            "NOT the raw FreeSWITCH event name."
        ),
    )
    payload = models.JSONField(
        help_text=(
            "Normalized event payload to be sent to the WebSocket client. "
            "Must conform to the application event schema. "
            "SECURITY: Must NOT contain SIP passwords, API keys, "
            "application passwords, or raw FreeSWITCH webhook payloads."
        ),
    )

    # ------------------------------------------------------------------
    # Delivery tracking
    # ------------------------------------------------------------------
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="UTC timestamp when this event was created.",
    )
    dispatched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC timestamp when this event was successfully published to Redis.",
    )
    status = models.CharField(
        max_length=20,
        choices=OutboxEventStatus.choices,
        default=OutboxEventStatus.PENDING,
        db_index=True,
        help_text="Delivery status of this outbox event.",
    )

    class Meta:
        db_table = "outbox_events"
        verbose_name = "Outbox Event"
        verbose_name_plural = "Outbox Events"
        ordering = ["created_at"]
        indexes = [
            # Partial index: matches the exact worker query (fetch pending events in order).
            # Ignores millions of dispatched rows, keeping the index tiny and memory-resident.
            models.Index(
                fields=["created_at"],
                name="idx_outbox_pending_created",
                condition=models.Q(status=OutboxEventStatus.PENDING),
            ),
            # Re-dispatch query: find failed events for retry
            models.Index(
                fields=["status", "tenant"],
                name="idx_outbox_status_tenant",
            ),
            # Target lookup: "find all pending events for user X"
            models.Index(
                fields=["target_type", "target_id", "status"],
                name="idx_outbox_target_status",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.event_type} → {self.target_type}:{self.target_id} "
            f"[{self.status}]"
        )

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent id={self.id} "
            f"event_type={self.event_type!r} "
            f"target={self.target_type}:{self.target_id} "
            f"status={self.status!r}>"
        )
