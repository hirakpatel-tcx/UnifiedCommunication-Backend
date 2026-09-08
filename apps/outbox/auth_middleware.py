"""
apps/outbox/auth_middleware.py
────────────────────────────────
ASGI middleware that authenticates WebSocket connections via a JWT access
token passed as a query parameter (browsers cannot set custom headers on
the WebSocket handshake, so the Authorization header used by REST is not
available here).

Usage: wss://<host>/ws/events/?token=<access_token>
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user_from_token(raw_token):
    from django.contrib.auth import get_user_model

    try:
        validated_token = AccessToken(raw_token)
        user_id = validated_token["user_id"]
    except (TokenError, InvalidToken, KeyError):
        return AnonymousUser()

    User = get_user_model()
    try:
        return User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    Resolves scope["user"] from a ?token=<JWT access token> query parameter.
    Falls back to AnonymousUser when the token is missing or invalid; the
    consumer is responsible for rejecting unauthenticated connections.
    """

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        token = parse_qs(query_string).get("token", [None])[0]

        scope["user"] = await _get_user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
