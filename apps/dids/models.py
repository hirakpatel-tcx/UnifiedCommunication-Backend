"""
apps/dids/models.py
────────────────────
DID (Direct Inward Dial) / Phone Number models.

Design decisions:

DID OWNERSHIP vs ASSIGNMENT:
  - DID belongs to a Tenant. The tenant owns the number.
  - A User is ASSIGNED access to a DID via the UserDID through table.
  - Assignment does NOT transfer ownership.
  - Multiple users can be assigned the same DID if business rules allow.

  Example:
    Tenant A
       └── DID +18321234567  (owned by Tenant A)
              ├── User A     (assigned — UserDID record)
              └── User B     (assigned — UserDID record)

FAX:
  - DID has NO fax capability. There is NO fax_enabled field.
  - Fax is represented solely by FaxBox (User.fax_boxes JSONField).
  - DID supports only: calling, messaging.

CONSTRAINTS:
  - (tenant, freeswitch_object_id) is unique — FreeSWITCH IDs are not
    globally unique; they are only unique within a tenant.

REASSIGNMENT:
  - DID can be removed and re-added to users via DIDService.
  - DIDService enforces that did.tenant == user.tenant before assigning.
"""

from django.db import models

from apps.common.models import TimestampedModel


# ---------------------------------------------------------------------------
# DID
# ---------------------------------------------------------------------------

class DID(TimestampedModel):
    """
    FreeSWITCH-managed phone number (DID), mirrored in Django.

    FreeSWITCH is the source of truth for DID state.
    Django mirrors the minimum state required for:
      - user assignment tracking
      - calling/messaging capability routing
      - realtime event routing

    Webhook sync:
      did.created — call FreeSWITCH API → create/sync local record (PLACEHOLDER)
      did.updated — call FreeSWITCH API → sync + update assignments (PLACEHOLDER)
      did.deleted — DO NOT call FreeSWITCH → remove assignments → delete record

    NEVER has a fax_enabled field. Fax capability is via FaxBox only.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="dids",
        help_text="The tenant that OWNS this phone number.",
    )
    freeswitch_object_id = models.CharField(
        max_length=255,
        help_text=(
            "The object_id assigned by FreeSWITCH to this DID. "
            "Unique within a tenant — not globally unique."
        ),
    )
    number = models.CharField(
        max_length=20,
        db_index=True,
        help_text=(
            "Phone number in E.164 format (e.g. '+18321234567'). "
            "Stored as provided by FreeSWITCH."
        ),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable name or label for this DID (e.g. 'Main Line', 'Support').",
    )

    # ------------------------------------------------------------------
    # Capabilities — calling and messaging ONLY.
    # There is NO fax_enabled field. Fax = FaxBox.
    # ------------------------------------------------------------------
    calling_enabled = models.BooleanField(
        default=False,
        help_text="This DID can be used for voice calling.",
    )
    messaging_enabled = models.BooleanField(
        default=False,
        help_text="This DID can be used for SMS/MMS messaging.",
    )
    # DO NOT add fax_enabled here. Fax capability is represented by FaxBox only.

    class Meta:
        db_table = "dids"
        verbose_name = "DID"
        verbose_name_plural = "DIDs"
        ordering = ["tenant", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "freeswitch_object_id"],
                name="uq_did_tenant_object_id",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "number"],
                name="idx_did_tenant_number",
            ),
        ]

    def __str__(self) -> str:
        caps = []
        if self.calling_enabled:
            caps.append("calling")
        if self.messaging_enabled:
            caps.append("messaging")
        cap_str = "+".join(caps) if caps else "no capabilities"
        return f"{self.number} ({self.tenant.tenant_code}) [{cap_str}]"

    def __repr__(self) -> str:
        return (
            f"<DID id={self.id} number={self.number!r} "
            f"tenant={self.tenant_id}>"
        )


# ---------------------------------------------------------------------------
# UserDID — through table
# ---------------------------------------------------------------------------

class UserDID(TimestampedModel):
    """
    Assignment record: grants a User access to a DID.

    This represents ACCESS, not ownership.
    The DID is still owned by the Tenant.

    Constraints:
      - (user, did) is unique — a user cannot be assigned the same DID twice.
      - DIDService enforces that user.tenant == did.tenant before creating
        a UserDID record. This constraint is not enforced at the DB level
        (it would require a cross-table check), so service-layer enforcement
        is mandatory.

    Multiple users can share the same DID:
      DID X → User A  (UserDID record)
      DID X → User B  (UserDID record)
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="user_dids",
        help_text="The user being granted access to this DID.",
    )
    did = models.ForeignKey(
        DID,
        on_delete=models.CASCADE,
        related_name="user_dids",
        help_text="The DID the user is being granted access to.",
    )

    class Meta:
        db_table = "user_dids"
        verbose_name = "User DID Assignment"
        verbose_name_plural = "User DID Assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "did"],
                name="uq_user_did_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_userdid_user"),
            models.Index(fields=["did"], name="idx_userdid_did"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.did.number}"

    def __repr__(self) -> str:
        return (
            f"<UserDID id={self.id} "
            f"user={self.user_id} did={self.did_id}>"
        )
