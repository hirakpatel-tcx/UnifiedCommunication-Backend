"""
apps/extensions/tasks.py
────────────────────────
Celery task that keeps the per-tenant presence cache fresh and pushes only the
changes to connected clients over the existing websocket.
"""

import logging

from celery import shared_task

from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.extensions.models import Extension
from apps.extensions.presence import (
    diff_presence, get_presence, presence_group, push_to_group, set_presence,
)
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


def compute_tenant_presence(tenant) -> dict | None:
    """Returns {extension_number: status} or None if the PBX could not be read."""
    from apps.extensions.views import _build_busy_extension_numbers, _extract_list

    response = FreeSwitchClientService.proxy_request(
        tenant=tenant, method="GET", endpoint_path="registrations/",
    )
    if response.status_code != 200:
        return None
    rows = _extract_list(response.data, "registrations")
    if rows is None:
        return None
    busy = _build_busy_extension_numbers(tenant)

    mapping = {}
    for row in rows:
        ext = row.get("extension")
        if not ext:
            continue
        st = row.get("status") or "offline"
        if st == "online" and ext in busy:
            st = "online_busy"
        mapping[ext] = st
    return mapping


@shared_task(name="apps.extensions.tasks.refresh_extension_presence")
def refresh_extension_presence():
    refreshed = 0
    for tenant in Tenant.objects.exclude(encrypted_api_key=""):
        if not (tenant.features or {}).get("calling", False):
            continue
        try:
            new = compute_tenant_presence(tenant)
            if new is None:
                continue
            old = get_presence(tenant.pk)
            set_presence(tenant.pk, new)
            changed = diff_presence(old, new)
            if changed and old:  # no diff push on the very first fill; clients use the snapshot
                user_by_ext = dict(
                    Extension.objects.filter(
                        tenant=tenant, extension_number__in=list(changed), user__isnull=False,
                    ).values_list("extension_number", "user_id")
                )
                push_to_group(
                    presence_group(tenant.pk),
                    "presence.update",
                    {"changes": [
                        {"extension_number": ext, "user_id": str(user_by_ext[ext]), "status": st}
                        for ext, st in changed.items() if ext in user_by_ext
                    ]},
                )
            refreshed += 1
        except Exception:
            logger.exception("presence refresh failed for tenant %s", tenant.pk)
    return refreshed
