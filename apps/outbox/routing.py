"""
apps/outbox/routing.py
WebSocket URL routing for the outbox event feed.
"""

from django.urls import re_path

from apps.outbox.consumers import UserEventsConsumer

websocket_urlpatterns = [
    re_path(r"^ws/events/$", UserEventsConsumer.as_asgi()),
]
