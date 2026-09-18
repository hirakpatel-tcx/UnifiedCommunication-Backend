"""
apps/downloads/models.py
─────────────────────────
DesktopDownloadToken — lets the welcome email link to the desktop installer
without ever putting the update-server's PUBLISH_TOKEN in an email.

The generated token is opaque and unrelated to PUBLISH_TOKEN. It is valid
for DOWNLOAD_TOKEN_TTL_HOURS (default 2h) starting from its FIRST use, so a
user can click the email link, have the download start, lose the connection,
and resume/retry from the same link until that window closes. Before first
use, the token has no expiry of its own (the email should still be treated
as sensitive, but the link doesn't silently rot before anyone clicks it).
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import TimestampedModel


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


class DesktopOS(models.TextChoices):
    WIN = "win", "Windows"
    MAC = "mac", "macOS"
    LINUX = "linux", "Linux"


class DesktopDownloadToken(TimestampedModel):
    """
    One issued link to the desktop installer for a given OS, scoped to the
    user it was generated for.
    """

    token = models.CharField(
        max_length=64,
        unique=True,
        default=_generate_token,
        editable=False,
        help_text="Opaque token embedded in the download URL. Not the update-server PUBLISH_TOKEN.",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="desktop_download_tokens",
    )
    os = models.CharField(max_length=8, choices=DesktopOS.choices)
    first_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set on first fetch; the 2-hour validity window is measured from this.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["token"]),
        ]

    def __str__(self):
        return f"DesktopDownloadToken({self.os}) for {self.user_id}"

    @property
    def ttl_hours(self) -> int:
        return getattr(settings, "DOWNLOAD_TOKEN_TTL_HOURS", 2)

    def is_expired(self) -> bool:
        if self.first_used_at is None:
            return False
        return timezone.now() > self.first_used_at + timezone.timedelta(hours=self.ttl_hours)

    def mark_used(self) -> None:
        if self.first_used_at is None:
            self.first_used_at = timezone.now()
            self.save(update_fields=["first_used_at", "updated_at"])
