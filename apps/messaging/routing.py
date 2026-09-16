"""
apps/messaging/routing.py
WebSocket URL routing for the per-conversation live message feed.
"""

from django.urls import re_path

from apps.messaging.consumers import ConversationConsumer

websocket_urlpatterns = [
    re_path(r"^ws/messaging/conversations/(?P<conversation_id>[0-9a-f-]+)/$", ConversationConsumer.as_asgi()),
]
