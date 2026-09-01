"""
apps/common/services/secret_service.py
──────────────────────────────────────
Fernet symmetric encryption service for sensitive data at rest
(SIP passwords, FreeSWITCH API keys).

SECURITY INVARIANTS:
- Plaintext secrets must only exist in-memory during encryption/decryption.
- Decrypted secrets are NEVER logged, serialized into standard REST API responses,
  saved into WebhookLog, or passed into Celery task arguments.
"""

import base64
import logging
# pyrefly: ignore [missing-import]
from cryptography.fernet import Fernet, InvalidToken
# pyrefly: ignore [missing-import]
from django.conf import settings
# pyrefly: ignore [missing-import]
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class SecretService:
    _fernet: Fernet | None = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet is not None:
            return cls._fernet

        key = getattr(settings, "ENCRYPTION_KEY", None)
        if not key:
            # Fallback for development if not explicitly configured in .env
            if settings.DEBUG:
                logger.warning("ENCRYPTION_KEY is not set. Using dev fallback Fernet key.")
                # Stable dev fallback key (generated specifically for local development fallback)
                key = "ZGV2LWZhbGxiYWNrLWtleS0zMi1ieXRlcy1sb25nLTEyMzQ="
            else:
                raise ImproperlyConfigured("ENCRYPTION_KEY must be configured in production.")

        try:
            # Fernet requires a 32-byte URL-safe base64-encoded key (44 characters with padding)
            if isinstance(key, str):
                key = key.strip()
                missing_padding = len(key) % 4
                if missing_padding:
                    key += "=" * (4 - missing_padding)
                key = key.encode("utf-8")
            cls._fernet = Fernet(key)
        except Exception as exc:
            raise ImproperlyConfigured(f"Invalid ENCRYPTION_KEY: {exc}") from exc

        return cls._fernet

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """Encrypts plaintext string into a Fernet base64 ciphertext string."""
        if plaintext is None:
            return ""
        fernet = cls._get_fernet()
        return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        """Decrypts Fernet base64 ciphertext string into plaintext."""
        if not ciphertext:
            return ""
        fernet = cls._get_fernet()
        try:
            return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            logger.error("Failed to decrypt secret: invalid token or encryption key mismatch.")
            raise ValueError("Decryption failed: invalid key or corrupted ciphertext.") from exc
