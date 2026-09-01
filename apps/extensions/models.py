"""
apps/extensions/models.py
──────────────────────────
Extension is a FreeSWITCH SIP extension resource.

Design decisions:
- A User can have 0..1 Extension (OneToOneField, nullable).
- An Extension can be unassigned (user=None) or reassigned.
- extension_number is unique only within a tenant (not globally).
  Tenant A and Tenant B can both have extension "101".
- SIP password is stored encrypted (SecretService.encrypt()).
  It is NEVER the same as the application password.
  Decryption happens only in-memory via SecretService.decrypt()
  for authorized SIP credential retrieval.
- transport_type is stored on Extension; SIP domain is managed
  on Tenant (default) and User (optional override).
- freeswitch_object_id is the stable FreeSWITCH reference for this extension.
  It is unique within a tenant (same object_id cannot appear twice
  in the same tenant — but different tenants can have the same object_id).

Constraints (DB-level):
  UNIQUE (tenant, freeswitch_object_id)
  UNIQUE (tenant, extension_number)

Security:
  encrypted_sip_password — never logged, never in API responses (standard),
  never in WebhookLog, never in WebSocket events.
  When SIP credentials change, only {"type": "sip.credentials.updated",
  "data": {"requires_refresh": true}} is emitted.
"""

from django.db import models

from apps.common.models import TimestampedModel


# ---------------------------------------------------------------------------
# Transport type choices
# ---------------------------------------------------------------------------

class TransportType(models.TextChoices):
    UDP = "UDP", "UDP"
    TCP = "TCP", "TCP"
    TLS = "TLS", "TLS"
    DTLS = "DTLS", "DTLS"


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

class Extension(TimestampedModel):
    """
    FreeSWITCH SIP extension, mirrored in Django for application use.

    FreeSWITCH is the source of truth for SIP credentials and extension state.
    Django mirrors the minimum state required for:
      - user assignment tracking
      - SIP credential delivery to authorized clients
      - realtime event routing

    Webhook sync:
      extension.created — data present in webhook → store without FreeSWITCH call
      extension.updated — call FreeSWITCH API → sync (PLACEHOLDER until API docs)
      extension.deleted — DO NOT call FreeSWITCH → delete local record
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="extensions",
        help_text="The tenant this extension belongs to.",
    )

    # ------------------------------------------------------------------
    # FreeSWITCH identity
    # ------------------------------------------------------------------
    freeswitch_object_id = models.CharField(
        max_length=255,
        help_text=(
            "The object_id assigned by FreeSWITCH to this extension. "
            "Unique within a tenant. Not globally unique."
        ),
    )

    # ------------------------------------------------------------------
    # Extension details
    # ------------------------------------------------------------------
    extension_number = models.CharField(
        max_length=20,
        help_text=(
            "The dialable extension number (e.g. '101'). "
            "Unique within a tenant — not globally unique. "
            "Tenant A and Tenant B may both have extension '101'."
        ),
    )
    sip_username = models.CharField(
        max_length=255,
        help_text=(
            "SIP username used for registration (e.g. '101-HVA'). "
            "This is the telephony identity, separate from the application email."
        ),
    )
    encrypted_sip_password = models.TextField(
        help_text=(
            "SecretService-encrypted SIP password. "
            "SECURITY: Decrypt only in-memory via SecretService.decrypt(). "
            "Never log, serialize in standard APIs, or include in WebSocket events. "
            "When credentials change, emit sip.credentials.updated "
            "with {requires_refresh: true} only — never the actual password."
        ),
    )

    # ------------------------------------------------------------------
    # SIP transport parameters
    # ------------------------------------------------------------------
    transport_type = models.CharField(
        max_length=10,
        choices=TransportType.choices,
        default=TransportType.UDP,
        help_text=(
            "SIP transport protocol. "
            "One of: UDP, TCP, TLS, DTLS. "
            "Required for SIP client registration."
        ),
    )

    # ------------------------------------------------------------------
    # User assignment (0..1)
    # ------------------------------------------------------------------
    user = models.OneToOneField(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extension",
        help_text=(
            "The User currently assigned to this extension. "
            "Nullable — an extension may exist unassigned. "
            "Assignment/unassignment is handled by ExtensionService "
            "to ensure atomicity and proper notification."
        ),
    )

    class Meta:
        db_table = "extensions"
        verbose_name = "Extension"
        verbose_name_plural = "Extensions"
        ordering = ["tenant", "extension_number"]
        constraints = [
            # FreeSWITCH object IDs are NOT globally unique — only unique per tenant
            models.UniqueConstraint(
                fields=["tenant", "freeswitch_object_id"],
                name="uq_extension_tenant_object_id",
            ),
            # Extension numbers are NOT globally unique — only unique per tenant
            models.UniqueConstraint(
                fields=["tenant", "extension_number"],
                name="uq_extension_tenant_number",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "sip_username"],
                name="idx_ext_tenant_sipuser",
            ),
        ]

    def __str__(self) -> str:
        assigned = f" → {self.user.email}" if self.user_id else " (unassigned)"
        return f"{self.extension_number} ({self.tenant.tenant_code}){assigned}"

    def __repr__(self) -> str:
        return (
            f"<Extension id={self.id} "
            f"number={self.extension_number!r} "
            f"tenant={self.tenant_id} "
            f"user={self.user_id}>"
        )

    # ------------------------------------------------------------------
    # Helpers (thin — business logic belongs in ExtensionService)
    # ------------------------------------------------------------------

    @property
    def is_assigned(self) -> bool:
        """Return True if this extension is currently assigned to a User."""
        return self.user_id is not None
