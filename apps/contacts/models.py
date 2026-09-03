"""
apps/contacts/models.py
────────────────────────
Data models for Contact and ContactNumber supporting both Company Directory
and User-Based (Personal) Directory with custom labeled phone numbers.
"""

from django.db import models
from apps.common.models import TimestampedModel


class DirectoryType(models.TextChoices):
    COMPANY = "company", "Company Directory"
    PERSONAL = "personal", "Personal Directory"


class Contact(TimestampedModel):
    """
    Unified contact model supporting both:
    - Company Directory: tenant-wide, shared, readable by all tenant users, managed by admins.
    - Personal Directory: user-scoped, private to the owner.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="contacts",
        help_text="Tenant that owns this contact record.",
    )
    owner = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="personal_contacts",
        help_text="The user who owns this personal contact. Null for company directory contacts.",
    )
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_contacts",
        help_text="The user who created this contact entry.",
    )
    directory_type = models.CharField(
        max_length=20,
        choices=DirectoryType.choices,
        default=DirectoryType.PERSONAL,
        db_index=True,
        help_text="Directory scope: company (tenant-wide) or personal (user-private).",
    )
    first_name = models.CharField(
        max_length=150,
        help_text="Contact first name (required).",
    )
    last_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Contact last name (optional).",
    )
    email = models.EmailField(
        blank=True,
        null=True,
        help_text="Contact email address (optional).",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Free-form notes about the contact.",
    )
    is_favorite = models.BooleanField(
        default=False,
        help_text="Whether this contact is pinned/favorited by the user.",
    )

    class Meta:
        db_table = "contacts"
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ["first_name", "last_name", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "directory_type"], name="idx_contact_tenant_dir"),
            models.Index(fields=["tenant", "owner"], name="idx_contact_tenant_owner"),
            models.Index(fields=["first_name", "last_name"], name="idx_contact_names"),
        ]

    @property
    def full_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name if name else self.first_name

    def __str__(self) -> str:
        return f"{self.full_name} ({self.directory_type})"


class ContactNumber(TimestampedModel):
    """
    Phone number associated with a Contact.
    A contact can have multiple phone numbers with identical or custom labels.
    """

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="numbers",
        help_text="Contact to which this phone number belongs.",
    )
    number = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Phone number or extension (e.g. +12025550199, 1001).",
    )
    label = models.CharField(
        max_length=50,
        default="Mobile",
        help_text="Label for this number (e.g. Mobile, Work, Home, Office, Custom).",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Indicates whether this is the primary dialing number for the contact.",
    )

    class Meta:
        db_table = "contact_numbers"
        verbose_name = "Contact Number"
        verbose_name_plural = "Contact Numbers"
        ordering = ["-is_primary", "created_at"]
        indexes = [
            models.Index(fields=["contact", "is_primary"], name="idx_contactnum_primary"),
            models.Index(fields=["number"], name="idx_contactnum_number"),
        ]

    def __str__(self) -> str:
        return f"{self.label}: {self.number}"
