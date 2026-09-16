"""
apps/messaging/consumers.py
─────────────────────────────
WebSocket consumer for live messages within one open conversation thread.

Distinct from apps.outbox.consumers.UserEventsConsumer (the global,
per-user feed used for inbox-level notifications like new-conversation /
unread-count updates): this consumer joins "conversation.{id}", delivering
every new Message in that thread in near-real-time while it's open, via the
same OutboxEvent → Celery → Channels pipeline already used tenant-wide (see
apps/outbox/models.py, target_type="conversation").

Unlike the personal user.{id} group (implicitly safe — a user can only ever
be added to their own), a conversation group requires an explicit
authorization check at connect time: the connecting user's tenant must own
the conversation, or the connection is rejected.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]

        allowed = await self._user_can_access_conversation(user, self.conversation_id)
        if not allowed:
            await self.close(code=4403)
            return

        self.group_name = f"conversation.{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Ignore any client-sent frames; this is a server-push-only feed —
    # sending a message still goes through POST /messaging/messages/send/.
    async def receive_json(self, content, **kwargs):
        pass

    async def outbox_event(self, event):
        """Handler for channel_layer.group_send({"type": "outbox.event", ...})."""
        await self.send_json({
            "event": event["event_type"],
            "data": event["payload"],
        })

    @database_sync_to_async
    def _user_can_access_conversation(self, user, conversation_id) -> bool:
        from apps.messaging.models import Conversation

        if user.is_superuser or getattr(user, "role", "") == "superadmin":
            return Conversation.objects.filter(id=conversation_id).exists()
        if not user.tenant_id:
            return False
        return Conversation.objects.filter(id=conversation_id, tenant_id=user.tenant_id).exists()
