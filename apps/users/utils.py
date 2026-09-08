"""
apps/users/utils.py
────────────────────
Small helpers for user provisioning.
"""

import secrets
import string


def generate_temp_password(length: int = 14) -> str:
    """
    Generates a random password suitable for a one-time temporary credential.
    Guarantees at least one lowercase, one uppercase, one digit, and one symbol.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd
