"""
apps/common/tenant_resolver.py
──────────────────────────────
Tenant resolution helper for scoped administrative requests.
Enforces that superadmins must explicitly pass a tenant identifier
(?tenant_id=... or X-Tenant-ID header), while regular tenant admins
are automatically scoped to their assigned tenant.
"""

import uuid
from rest_framework.exceptions import ValidationError
from apps.tenants.models import Tenant


def get_scoped_tenant(request):
    """
    Returns the target Tenant for the current request.

    Rules:
    - For superadmin: 'tenant_id' query param or 'X-Tenant-ID' header is strictly REQUIRED.
      Accepts internal UUID, FreeSWITCH tenant UUID, or tenant code (case-insensitive).
    - For tenant admin: automatically uses request.user.tenant.
    - If tenant cannot be found or resolved, raises ValidationError.
    """
    user = request.user
    body = getattr(request, "data", None)
    body_tenant = body.get("tenant_id") if hasattr(body, "get") else None
    raw_tenant = (
        request.query_params.get("tenant_id")
        or request.headers.get("X-Tenant-ID")
        or body_tenant
    )

    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        if not raw_tenant:
            raise ValidationError(
                {"tenant_id": "tenant_id (query parameter, request body, or X-Tenant-ID header) is required for superadmin requests."}
            )

        raw_str = str(raw_tenant).strip()
        tenant = None

        # 1. Try by primary key UUID
        try:
            val_uuid = uuid.UUID(raw_str)
            tenant = Tenant.objects.filter(id=val_uuid).first()
            if not tenant:
                tenant = Tenant.objects.filter(freeswitch_tenant_uuid=val_uuid).first()
        except (ValueError, AttributeError):
            pass

        # 2. Try by tenant_code (e.g. TCX, HVA, GMD)
        if not tenant:
            tenant = Tenant.objects.filter(tenant_code__iexact=raw_str).first()

        if not tenant:
            raise ValidationError({"tenant_id": f"Tenant '{raw_str}' was not found."})

        return tenant

    # Tenant admin
    if user.tenant:
        return user.tenant

    raise ValidationError({"detail": "User does not belong to any tenant."})
