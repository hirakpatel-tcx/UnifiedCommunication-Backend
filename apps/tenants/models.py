"""
apps/tenants/models.py
──────────────────────
Tenant is the top-level multi-tenant entity.

Design decisions:
- freeswitch_tenant_uuid is the stable external identity (never changes).
- tenant_code is informational only; it can change and must NOT be used as a FK.
- encrypted_api_key is stored using SecretService (Fernet-encrypted).
  The plaintext key is never stored, logged, or passed through Celery args.
- features is a validated JSONField controlling which capabilities are active
  for this tenant, independent of user-level permissions.
- Each tenant has its own API key — there is NO global FreeSWITCH key.
"""

from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import TimestampedModel

# ---------------------------------------------------------------------------
# Feature key constants
# ---------------------------------------------------------------------------
FEATURE_CALLING = "calling"
FEATURE_MESSAGING = "messaging"
FEATURE_FAX = "fax"
FEATURE_VOICEMAIL = "voicemail"

VALID_FEATURE_KEYS = frozenset(
    {FEATURE_CALLING, FEATURE_MESSAGING, FEATURE_FAX, FEATURE_VOICEMAIL}
)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_tenant_features(value: dict) -> None:
    """
    Validates the Tenant.features JSONField.

    Expected structure:
        {
            "calling":   bool,
            "messaging": bool,
            "fax":       bool,
            "voicemail": bool,
        }

    Rules:
    - Must be a dict.
    - All keys must be in VALID_FEATURE_KEYS.
    - All values must be booleans.
    - No extra keys are allowed (strict validation).
    """
    if not isinstance(value, dict):
        raise ValidationError(
            "features must be a JSON object (dict).",
            code="features_not_dict",
        )

    unknown_keys = set(value.keys()) - VALID_FEATURE_KEYS
    if unknown_keys:
        raise ValidationError(
            f"Unknown feature key(s): {sorted(unknown_keys)}. "
            f"Valid keys are: {sorted(VALID_FEATURE_KEYS)}.",
            code="features_unknown_key",
        )

    for key, val in value.items():
        if not isinstance(val, bool):
            raise ValidationError(
                f"Feature '{key}' must be a boolean (true/false), got {type(val).__name__}.",
                code="features_value_not_bool",
            )


# ---------------------------------------------------------------------------
# Default features factory
# ---------------------------------------------------------------------------

def default_tenant_features() -> dict:
    """Returns the default tenant feature set — all features disabled."""
    return {
        FEATURE_CALLING: False,
        FEATURE_MESSAGING: False,
        FEATURE_FAX: False,
        FEATURE_VOICEMAIL: False,
    }


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class Tenant(TimestampedModel):
    """
    Represents one customer/organization (tenant) in the multi-tenant platform.

    Corresponds 1:1 with a FreeSWITCH tenant.

    Fields:
        freeswitch_tenant_uuid  — stable FreeSWITCH-assigned UUID, used as
                                   the canonical external reference. Never changes.
        tenant_code             — short mnemonic (e.g. "HVA"). Can change;
                                   never used as a FK or URL identifier.
        tenant_name             — human-readable name.
        encrypted_api_key       — SecretService-encrypted FreeSWITCH API key.
                                   The plaintext must never be stored, logged,
                                   or put into Celery task arguments.
        features                — dict of {feature_name: bool}. Controls
                                   what capabilities are available to this
                                   tenant's users regardless of permissions.
        is_active               — inactive tenants are treated as disabled.
    """

    freeswitch_tenant_uuid = models.UUIDField(
        unique=True,
        db_index=True,
        help_text=(
            "The stable UUID assigned by FreeSWITCH to this tenant. "
            "Used as the canonical external reference across the system. "
            "This value must never change after creation."
        ),
    )
    tenant_code = models.CharField(
        max_length=50,
        db_index=True,
        help_text=(
            "Short mnemonic code for this tenant (e.g. 'HVA', 'GMD'). "
            "This value can change. Never use it as a FK or URL identifier."
        ),
    )
    tenant_name = models.CharField(
        max_length=255,
        help_text="Human-readable display name for this tenant.",
    )
    encrypted_api_key = models.TextField(
        help_text=(
            "SecretService-encrypted FreeSWITCH API key for this tenant. "
            "This is per-tenant — there is no shared global key. "
            "SECURITY: Decrypt only in-memory via SecretService.decrypt(). "
            "Never log, serialize, or return the plaintext value."
        ),
    )
    features = models.JSONField(
        default=default_tenant_features,
        validators=[validate_tenant_features],
        help_text=(
            "Dict of enabled features for this tenant. "
            "Valid keys: calling, messaging, fax, voicemail. "
            "Values must be booleans. "
            "Example: {\"calling\": true, \"messaging\": true, \"fax\": false, \"voicemail\": true}"
        ),
    )
    sip_domain = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Default SIP domain / registrar host for all users in this tenant "
            "(e.g. 'sip.example.com' or 'pbx.tenant.com')."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive tenants are disabled system-wide.",
    )

    class Meta:
        db_table = "tenants"
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ["tenant_name"]

    def __str__(self) -> str:
        return f"{self.tenant_name} ({self.tenant_code})"

    def __repr__(self) -> str:
        return (
            f"<Tenant id={self.id} "
            f"freeswitch_uuid={self.freeswitch_tenant_uuid} "
            f"code={self.tenant_code!r}>"
        )

    # ------------------------------------------------------------------
    # Feature helpers — thin convenience methods; business logic belongs
    # in TenantService.
    # ------------------------------------------------------------------

    def is_feature_enabled(self, feature: str) -> bool:
        """Return True if the named feature is enabled for this tenant."""
        return bool(self.features.get(feature, False))

    @property
    def calling_enabled(self) -> bool:
        return self.is_feature_enabled(FEATURE_CALLING)

    @property
    def messaging_enabled(self) -> bool:
        return self.is_feature_enabled(FEATURE_MESSAGING)

    @property
    def fax_enabled(self) -> bool:
        return self.is_feature_enabled(FEATURE_FAX)

    @property
    def voicemail_enabled(self) -> bool:
        return self.is_feature_enabled(FEATURE_VOICEMAIL)
