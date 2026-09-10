"""
apps/common/models.py
─────────────────────
Abstract base models shared across all apps.

Rules:
- Every concrete model must use UUID as its primary key.
- Every concrete model should include created_at / updated_at timestamps.
- These are abstract — they create no database tables.
"""

import uuid

from django.db import models


class UUIDModel(models.Model):
    """
    Abstract base that replaces the default integer PK with a UUID.

    Using uuid.uuid4 (random UUID, v4) — does not leak ordering information
    or creation time, which is preferable for externally-visible IDs.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier (UUID v4).",
    )

    class Meta:
        abstract = True


class TimestampedModel(UUIDModel):
    """
    Abstract base that adds auto-managed created_at and updated_at timestamps.
    Inherits UUIDModel so every timestamped model also has a UUID PK.
    """

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="UTC timestamp of record creation.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="UTC timestamp of last record update.",
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class FaxTagStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"


class FaxTag(TimestampedModel):
    """
    Local workflow metadata for a FreeSWITCH fax transmission (medical review
    triage). FreeSWITCH remains the source of truth for the fax transmission
    itself (PDF, sender, timestamps) — this table only stores which user is
    handling the fax and its review status, keyed by the FreeSWITCH
    fax_file_uuid, merged onto the proxied fax listing on read (same pattern
    as apps.contacts annotating CDR/voicemail).
    """

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="fax_tags"
    )
    fax_file_uuid = models.CharField(
        max_length=64,
        help_text="FreeSWITCH fax transmission UUID (fax_file_uuid) this record applies to.",
    )
    assigned_to = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_faxes",
        help_text="User (with an extension) this fax has been assigned to for review.",
    )
    assigned_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(
        max_length=16, choices=FaxTagStatus.choices, default=FaxTagStatus.PENDING
    )
    status_updated_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "fax_file_uuid"], name="uniq_fax_tag_per_transmission"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "fax_file_uuid"]),
        ]

    def __str__(self):
        return f"FaxTag({self.fax_file_uuid}) status={self.status}"
