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
from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.common.tenant_resolver import get_scoped_tenant
from apps.common.tl_scoping import resolve_tl_extensions
from apps.extensions.models import Extension
from apps.extensions.serializers import ExtensionSerializer, ExtensionTransportUpdateSerializer
from apps.users.serializers import UserDetailSerializer


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

        # Team Leads are restricted to the extensions of callers assigned to
        # their granted DID/Department combinations (TLGroupAccess). Grants
        # resolving to zero extensions correctly yield an empty listing
        # rather than falling back to the tenant-wide list.
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", "") == "superadmin"):
            tl_extensions = resolve_tl_extensions(user)
            if tl_extensions is not None:
                qs = qs.filter(extension_number__in=tl_extensions)

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


def _extract_list(response_data, wrapper_key):
    """
    tcxconnect's client_api endpoints wrap their row list under a named key
    (e.g. {"registrations": [...]}), but some deployments/proxies return the
    bare list instead. Handle both shapes.
    """
    if isinstance(response_data, dict) and isinstance(response_data.get(wrapper_key), list):
        return response_data[wrapper_key]
    if isinstance(response_data, list):
        return response_data
    return None


def _enrich_rows_with_user(rows, tenant, key, lookup_field):
    """
    Attaches the UnifiedCommunication user assigned to each row's extension,
    under a new "user" key (None when no Extension/User match is found).

    `key` is the row field holding the extension identifier; `lookup_field`
    is the Extension model field it should be matched against
    ("extension_number" or "sip_username", depending on what the upstream
    tcxconnect endpoint reports).
    """
    values = [row.get(key) for row in rows]
    extensions_by_value = {
        getattr(ext, lookup_field): ext
        for ext in Extension.objects.filter(
            tenant=tenant, **{f"{lookup_field}__in": values}
        ).select_related("user__tenant", "user__extension").prefetch_related(
            "user__user_dids__did",
            "user__did_department_assignments__department",
            "user__tl_group_access__group__entries__department",
        )
    }
    for row in rows:
        ext = extensions_by_value.get(row.get(key))
        user = getattr(ext, "user", None) if ext else None
        row["user"] = UserDetailSerializer(user).data if user else None


class ExtensionRegistrationsView(APIView):
    """
    GET /api/v1/extensions/registrations/
    Online/offline status for every enabled extension in the tenant, with
    extension number and name, proxied from FreeSWITCH via ESL. Each row is
    enriched with the UnifiedCommunication user assigned to that extension,
    if any.
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        response = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="registrations/",
        )

        registrations = _extract_list(response.data, "registrations")
        if response.status_code == status.HTTP_200_OK and registrations is not None:
            _enrich_rows_with_user(registrations, tenant, key="extension", lookup_field="extension_number")

        return response


class ExtensionActiveCallsView(APIView):
    """
    GET /api/v1/extensions/active-calls/
    Live active calls for the tenant, each with the connected extension,
    proxied from FreeSWITCH via ESL. Each row is enriched with the
    UnifiedCommunication user assigned to that extension, if any.
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        response = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="calls/",
        )

        calls = _extract_list(response.data, "calls")
        if response.status_code == status.HTTP_200_OK and calls is not None:
            _enrich_rows_with_user(calls, tenant, key="extension", lookup_field="sip_username")

        return response
