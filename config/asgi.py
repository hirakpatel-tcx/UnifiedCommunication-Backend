"""
config/asgi.py
ASGI config for UnifiedCommunication-Backend.
Used by Django Channels (WebSockets) and Daphne.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.outbox.auth_middleware import JWTAuthMiddlewareStack  # noqa: E402
from apps.outbox.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JWTAuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
