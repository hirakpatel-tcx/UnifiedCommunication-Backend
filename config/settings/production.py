"""config/settings/production.py"""

from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # noqa: F405

# Security
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = False  # nginx handles SSL termination
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[
        "https://api.tcxconnect.tech",
        "https://app.tcxconnect.tech",
    ],
)

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "https://api.tcxconnect.tech",
        "https://app.tcxconnect.tech",
    ],
)
CORS_ALLOW_CREDENTIALS = True

# Only JSON in production
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}
