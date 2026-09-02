"""
apps/dids/views.py
──────────────────
REST API views for DID listing and details.
"""

from rest_framework import generics
from apps.common.permissions import IsAdminOrSuperAdmin
from apps.common.tenant_resolver import get_scoped_tenant
from apps.dids.models import DID
from apps.dids.serializers import DIDSerializer


class DIDListView(generics.ListAPIView):
    """
    GET /api/v1/dids/
    Lists DIDs scoped to a specific tenant.
    For superadmin: 'tenant_id' query parameter or 'X-Tenant-ID' header is required.
    For admin: automatically scoped to the user's tenant.
    """
    serializer_class = DIDSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        qs = DID.objects.filter(tenant=tenant).select_related("tenant").prefetch_related("user_dids__user")

        # Search query (by phone number)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(number__icontains=search)

        return qs.order_by("number")


class DIDDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/dids/{id}/
    Retrieves single DID details.
    Restricted to superadmin and admin roles.
    """
    serializer_class = DIDSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        qs = DID.objects.select_related("tenant").prefetch_related("user_dids__user").all()
        if user.is_superuser or user.role == "superadmin":
            return qs
        if user.tenant_id:
            return qs.filter(tenant_id=user.tenant_id)
        return qs.none()
