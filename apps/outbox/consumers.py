"""
apps/outbox/consumers.py
──────────────────────────
WebSocket consumer for outbound application events (extension.updated,
did.updated, sip.credentials.updated, etc.) delivered via the OutboxEvent
pattern — see apps/outbox/models.py and apps/outbox/tasks.py.

Each authenticated connection joins exactly one personal group,
"user:{user.id}", matching the target_type="user" convention documented
on OutboxEvent. Tenant-wide groups are not joined here; they require
explicit authorization and are out of scope for this personal-events feed.
"""

import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class UserEventsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.group_name = f"user.{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Ignore any client-sent frames; this is a server-push-only feed.
    async def receive_json(self, content, **kwargs):
        pass

    async def outbox_event(self, event):
        """
        Handler for messages sent via channel_layer.group_send with
        {"type": "outbox.event", ...} (Channels maps "outbox.event" -> outbox_event).
        """
        await self.send_json({
            "event": event["event_type"],
            "data": event["payload"],
        })
