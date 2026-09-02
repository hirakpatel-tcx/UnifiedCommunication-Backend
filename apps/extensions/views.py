"""
apps/extensions/views.py
────────────────────────
REST API views for Extension listing, details, and transport modification.
"""

from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import BasePermission
from django.shortcuts import get_object_or_404

from apps.common.permissions import IsAdminOrSuperAdmin, IsSuperAdmin
from apps.common.tenant_resolver import get_scoped_tenant
from apps.extensions.models import Extension
from apps.extensions.serializers import ExtensionSerializer, ExtensionTransportUpdateSerializer


class IsSuperAdminOrReadOnlyAdmin(BasePermission):
    """
    Read (GET/HEAD/OPTIONS): Allowed for tenant admins (scoped) and superadmins.
    Write (PATCH/PUT/DELETE): Allowed ONLY for superadmin.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        is_super = user.is_superuser or getattr(user, "role", "") == "superadmin"
        if is_super:
            return True
        if request.method in ("GET", "HEAD", "OPTIONS") and getattr(user, "role", "") == "admin":
            return True
        return False


class ExtensionListView(generics.ListAPIView):
    """
    GET /api/v1/extensions/
    Lists extensions scoped to a specific tenant.
    For superadmin: 'tenant_id' query parameter or 'X-Tenant-ID' header is required.
    For admin: automatically scoped to the user's tenant.
    """
    serializer_class = ExtensionSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        qs = Extension.objects.filter(tenant=tenant).select_related("tenant", "user")

        # Assignment filtering: is_assigned=true / false
        is_assigned = self.request.query_params.get("is_assigned")
        if is_assigned is not None:
            if is_assigned.lower() in ("true", "1"):
                qs = qs.filter(user__isnull=False)
            elif is_assigned.lower() in ("false", "0"):
                qs = qs.filter(user__isnull=True)

        # Search query
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(extension_number__icontains=search) | qs.filter(sip_username__icontains=search)

        return qs.order_by("extension_number")


class ExtensionDetailView(generics.RetrieveUpdateAPIView):
    """
    GET   /api/v1/extensions/{id}/  (Admin or Superadmin)
    PATCH /api/v1/extensions/{id}/  (Superadmin ONLY)
    Retrieves single extension details or updates transport_type.
    """
    serializer_class = ExtensionSerializer
    permission_classes = [IsSuperAdminOrReadOnlyAdmin]
    lookup_field = "id"
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = Extension.objects.select_related("tenant", "user").all()
        if user.is_superuser or getattr(user, "role", "") == "superadmin":
            return qs
        if getattr(user, "role", "") == "admin" and user.tenant_id:
            return qs.filter(tenant_id=user.tenant_id)
        return qs.none()


class ExtensionTransportUpdateView(APIView):
    """
    PATCH /api/v1/extensions/{id}/transport/
    POST  /api/v1/extensions/{id}/transport/

    Updates the SIP transport type (UDP, TCP, TLS, DTLS) for an extension.
    RESTRICTED: Only superadmin can change transport type.
    """
    permission_classes = [IsSuperAdmin]

    def patch(self, request, id, *args, **kwargs):
        extension = get_object_or_404(Extension, id=id)

        serializer = ExtensionTransportUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_transport = serializer.validated_data["transport_type"]
        extension.transport_type = new_transport
        extension.save(update_fields=["transport_type", "updated_at"])

        return Response(
            {
                "id": str(extension.id),
                "extension_number": extension.extension_number,
                "sip_username": extension.sip_username,
                "transport_type": extension.transport_type,
                "updated_at": extension.updated_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, id, *args, **kwargs):
        return self.patch(request, id, *args, **kwargs)
