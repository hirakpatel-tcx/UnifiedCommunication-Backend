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

from channels.db import database_sync_to_async
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

        # Extension presence feed: scoped to the user's OWN tenant only.
        self.presence_group_name = None
        tenant_id = getattr(user, "tenant_id", None)
        if tenant_id:
            from apps.extensions.presence import presence_group
            self.presence_group_name = presence_group(tenant_id)
            await self.channel_layer.group_add(self.presence_group_name, self.channel_name)

        await self.accept()

        if self.presence_group_name:
            snapshot = await self._presence_snapshot(user)
            await self.send_json({"event": "presence.snapshot", "data": {"presence": snapshot}})

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if getattr(self, "presence_group_name", None):
            await self.channel_layer.group_discard(self.presence_group_name, self.channel_name)

    @database_sync_to_async
    def _presence_snapshot(self, user):
        from apps.extensions.models import Extension
        from apps.extensions.presence import get_presence

        cached = get_presence(user.tenant_id)
        rows = Extension.objects.filter(
            tenant_id=user.tenant_id, user__isnull=False,
        ).values_list("extension_number", "user_id")
        return [
            {
                "user_id": str(uid),
                "extension_number": ext,
                "status": cached.get(ext, "offline"),
            }
            for ext, uid in rows
        ]

    async def presence_update(self, event):
        """group_send {"type": "presence.update", "changes": [...]} -> client."""
        await self.send_json({"event": "presence.update", "data": {"changes": event["changes"]}})

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
