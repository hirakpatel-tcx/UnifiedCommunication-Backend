"""
apps/extensions/presence.py
───────────────────────────
Per-tenant extension presence (online / online_busy / offline), cached so the
REST list can be served without a PBX round-trip, and pushed over the existing
`ws/events/` socket as `presence.update` events.

Cache shape (per tenant):  {extension_number: "online" | "online_busy" | "offline"}
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

logger = logging.getLogger(__name__)

PRESENCE_CACHE_KEY = "ext_presence:{tenant_id}"
PRESENCE_CACHE_TTL = 120  # seconds; refreshed every few seconds by the beat task


def presence_group(tenant_id) -> str:
    return f"presence.{tenant_id}"


def get_presence(tenant_id) -> dict:
    try:
        return cache.get(PRESENCE_CACHE_KEY.format(tenant_id=tenant_id)) or {}
    except Exception:
        return {}


def set_presence(tenant_id, mapping: dict) -> None:
    try:
        cache.set(PRESENCE_CACHE_KEY.format(tenant_id=tenant_id), mapping, PRESENCE_CACHE_TTL)
    except Exception:
        logger.warning("presence cache write failed", exc_info=True)


def diff_presence(old: dict, new: dict) -> dict:
    """Entries whose status changed (or are new). Vanished extensions -> offline."""
    changed = {ext: st for ext, st in new.items() if old.get(ext) != st}
    for ext in old:
        if ext not in new and old[ext] != "offline":
            changed[ext] = "offline"
    return changed


def push_to_group(group: str, event_type: str, payload: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {"type": event_type, **payload})
    except Exception:
        logger.warning("presence push to %s failed", group, exc_info=True)
