"""
apps/outbox/tasks.py
─────────────────────
Celery worker: dispatches pending OutboxEvent rows to Django Channels.

Flow (see apps/outbox/models.py for the full design):
    OutboxEvent(status=pending) → dispatch_pending_outbox_events (this task)
        → channel_layer.group_send(f"{target_type}:{target_id}", ...)
        → OutboxEvent.status = dispatched (or failed, retained for re-dispatch)
"""

import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.utils import timezone

from apps.outbox.models import OutboxEvent, OutboxEventStatus

logger = logging.getLogger(__name__)

# Cap per run so one worker tick can't be starved by an unbounded backlog.
BATCH_SIZE = 500


@shared_task(name="apps.outbox.tasks.dispatch_pending_outbox_events")
def dispatch_pending_outbox_events():
    """
    Fetches pending OutboxEvent rows in creation order and publishes each
    to its target's Channels group. Marks each row dispatched or failed
    individually so one bad event doesn't block the rest of the batch.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.error("dispatch_pending_outbox_events: no channel layer configured")
        return 0

    events = list(
        OutboxEvent.objects.filter(status=OutboxEventStatus.PENDING)
        .order_by("created_at")[:BATCH_SIZE]
    )
    dispatched_count = 0

    for event in events:
        group_name = f"{event.target_type}.{event.target_id}"
        message = {
            "type": "outbox.event",
            "event_type": event.event_type,
            "payload": event.payload,
        }
        try:
            async_to_sync(channel_layer.group_send)(group_name, message)
        except Exception as exc:
            logger.error(
                "Failed to dispatch OutboxEvent %s (%s -> %s): %s",
                event.id, event.event_type, group_name, exc, exc_info=True,
            )
            OutboxEvent.objects.filter(id=event.id).update(status=OutboxEventStatus.FAILED)
            continue

        OutboxEvent.objects.filter(id=event.id).update(
            status=OutboxEventStatus.DISPATCHED,
            dispatched_at=timezone.now(),
        )
        dispatched_count += 1

    return dispatched_count
