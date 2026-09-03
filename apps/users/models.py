"""
apps/users/models.py
─────────────────────
Custom User model — the central application identity.

Design decisions:
- Extends AbstractBaseUser (not AbstractUser) for full control over fields.
- email is the login identity and is GLOBALLY UNIQUE across all tenants.
  A single email cannot exist in more than one tenant.
- Every User belongs to exactly ONE Tenant. This includes superadmins.
- password is the APPLICATION password (Django-managed, one-way hash).
  It is completely separate from the SIP password, which lives on Extension.
- fax_boxes and voicemail_boxes are JSONFields on User (not separate tables).
  They are validated structurally at the model level; FreeSWITCH tenant
  ownership is verified at the service layer before writes.
- Role is stored on the model but is NOT the complete authorization system.
  Permissions are first-class (via Django's PermissionsMixin).
- is_active controls login; disabling a User does NOT touch FreeSWITCH.

Security invariants:
- Application password: one-way hash via set_password() / check_password().
  Never logged, returned in APIs, sent over WebSocket, or in Celery args.
- SIP password: lives on Extension; encrypted and completely separate.
- User.fax_boxes and User.voicemail_boxes must never contain secrets.
"""

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from .managers import UserManager
from .validators import validate_fax_boxes, validate_voicemail_boxes


# ---------------------------------------------------------------------------
# Role choices
# ---------------------------------------------------------------------------

class UserRole(models.TextChoices):
    SUPERADMIN = "superadmin", "Superadmin"
    ADMIN = "admin", "Admin"
    USER = "user", "User"


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(AbstractBaseUser, PermissionsMixin):
    """
    Application user — central identity in the multi-tenant UC system.

    Belongs to:
        One Tenant (mandatory, immutable after creation for normal users).

    Has optional telephony resources:
        0..1 Extension   (via Extension.user OneToOneField reverse)
        0..n DIDs        (via UserDID through table)
        0..n FaxBoxes    (via fax_boxes JSONField)
        0..n VoicemailBoxes (via voicemail_boxes JSONField)

    Authentication:
        USERNAME_FIELD = 'email'  →  login uses email + application password.
        SIP credentials are on Extension; completely separate.
    """

    # ------------------------------------------------------------------
    # Primary key — UUID v4
    # ------------------------------------------------------------------
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    # ------------------------------------------------------------------
    # Core identity
    # ------------------------------------------------------------------
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        help_text=(
            "The tenant this user belongs to. Optional for platform superusers. "
            "Mandatory for tenant-scoped users and admins."
        ),
    )
    email = models.EmailField(
        unique=True,
        help_text=(
            "Application login email. "
            "Globally unique across ALL tenants — two tenants cannot share an email. "
            "Stored in lowercase, stripped of whitespace (enforced by UserManager). "
            "This is the application identity; SIP username lives on Extension."
        ),
    )

    # ------------------------------------------------------------------
    # Role
    # ------------------------------------------------------------------
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
        db_index=True,
        help_text=(
            "Primary role. NOT the complete authorization system — "
            "permissions are first-class. "
            "superadmin: platform-level admin + communication user of own tenant. "
            "admin: tenant-scoped admin. "
            "user: normal user."
        ),
    )

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "Inactive users cannot authenticate. "
            "Disabling a User does NOT automatically delete their FreeSWITCH extension. "
            "Application user lifecycle and FreeSWITCH resource lifecycle are separate."
        ),
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Designates whether the user can log into the Django admin site.",
    )
    is_first_login = models.BooleanField(
        default=True,
        help_text="Designates whether this user has never logged in before.",
    )
    must_change_password = models.BooleanField(
        default=True,
        help_text="Designates whether the user is required to change their password upon login.",
    )
    first_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
    )
    last_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
    )

    # ------------------------------------------------------------------
    # SIP Domain (per-user override; falls back to tenant.sip_domain)
    # ------------------------------------------------------------------
    sip_domain = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Custom SIP domain / registrar host for this user. "
            "If blank, automatically inherits the tenant's default sip_domain."
        ),
    )

    # ------------------------------------------------------------------
    # FaxBox assignment (JSON)
    # Exact structure:
    #   [{"fax_uuid": "...", "fax_caller_id_name": "...", "fax_caller_id_number": "..."}]
    # Multiple users can share the same fax_uuid.
    # FreeSWITCH tenant ownership is verified by FaxBoxService before writes.
    # ------------------------------------------------------------------
    fax_boxes = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_fax_boxes],
        help_text=(
            "List of FaxBox assignments. "
            "Structure: [{\"fax_uuid\": str, \"fax_caller_id_name\": str, "
            "\"fax_caller_id_number\": str}, ...]. "
            "Multiple users may share the same fax_uuid. "
            "WRITE ONLY via FaxBoxService — direct assignment bypasses "
            "FreeSWITCH tenant ownership verification."
        ),
    )

    # ------------------------------------------------------------------
    # VoicemailBox assignment (JSON)
    # Exact structure: [101, 1001]
    # Multiple users can share the same voicemail_box_id.
    # FreeSWITCH tenant ownership is verified by VoicemailBoxService before writes.
    # ------------------------------------------------------------------
    voicemail_boxes = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_voicemail_boxes],
        help_text=(
            "List of voicemail box IDs this user has access to. "
            "Structure: [int, ...]. "
            "Multiple users may share the same voicemail_box_id. "
            "WRITE ONLY via VoicemailBoxService — direct assignment bypasses "
            "FreeSWITCH tenant ownership verification."
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ------------------------------------------------------------------
    # Django auth configuration
    # ------------------------------------------------------------------
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"

    # tenant is required for creation but not in REQUIRED_FIELDS
    # (it's passed as a kwarg to create_user/create_superuser)
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["email"]
        indexes = [
            models.Index(fields=["tenant", "role"], name="idx_user_tenant_role"),
            models.Index(fields=["tenant", "is_active"], name="idx_user_tenant_active"),
            GinIndex(fields=["voicemail_boxes"], name="idx_user_vmboxes_gin"),
            GinIndex(fields=["fax_boxes"], name="idx_user_faxboxes_gin"),
        ]

    def __str__(self) -> str:
        return self.email

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} email={self.email!r} "
            f"role={self.role!r} tenant={self.tenant_id}>"
        )

    # ------------------------------------------------------------------
    # Role helpers
    # ------------------------------------------------------------------

    @property
    def is_superadmin(self) -> bool:
        return self.role == UserRole.SUPERADMIN

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def effective_sip_domain(self) -> str:
        """
        Returns the user's custom sip_domain if configured,
        otherwise falls back to the tenant's default sip_domain.
        """
        if self.sip_domain:
            return self.sip_domain
        if self.tenant and self.tenant.sip_domain:
            return self.tenant.sip_domain
        return ""

    # ------------------------------------------------------------------
    # FaxBox helpers (thin — business logic belongs in FaxBoxService)
    # ------------------------------------------------------------------

    def get_fax_uuids(self) -> list[str]:
        """Return list of fax_uuid strings assigned to this user."""
        return [entry["fax_uuid"] for entry in self.fax_boxes]

    def has_fax_box(self, fax_uuid: str) -> bool:
        """Return True if the given fax_uuid is assigned to this user."""
        return fax_uuid in self.get_fax_uuids()

    # ------------------------------------------------------------------
    # VoicemailBox helpers
    # ------------------------------------------------------------------

    def has_voicemail_box(self, box_id: int) -> bool:
        """Return True if the given voicemail_box_id is assigned to this user."""
        return box_id in self.voicemail_boxes
