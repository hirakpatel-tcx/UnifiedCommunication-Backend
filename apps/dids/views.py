"""
apps/dids/views.py
──────────────────
REST API views for DID listing and details.
"""

from rest_framework import generics
from apps.common.permissions import IsAdminOrSuperAdmin
from apps.common.tenant_resolver import get_scoped_tenant
from apps.dids.models import (
    DID,
    Department,
    UserDIDDepartmentAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)
from apps.dids.serializers import (
    DIDSerializer,
    DepartmentSerializer,
    UserDIDDepartmentAssignmentSerializer,
    AccessGroupSerializer,
    AccessGroupEntrySerializer,
    TLGroupAccessSerializer,
)


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


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class DepartmentListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/departments/  — list all departments (global, not tenant-scoped).
    POST /api/v1/dids/departments/  — create a department.
    """
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = Department.objects.all().order_by("name")


class DepartmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/departments/{id}/
    """
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = Department.objects.all()
    lookup_field = "id"


# ---------------------------------------------------------------------------
# UserDIDDepartmentAssignment — caller work assignment
# ---------------------------------------------------------------------------

class UserDIDDepartmentAssignmentListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/department-assignments/  — list assignments.
         Filter by ?user_id=, ?did_id=, ?department_id=.
    POST /api/v1/dids/department-assignments/  — assign a caller to a DID + Department.
    """
    serializer_class = UserDIDDepartmentAssignmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        qs = UserDIDDepartmentAssignment.objects.select_related("user", "did", "department")
        user_id = self.request.query_params.get("user_id")
        did_id = self.request.query_params.get("did_id")
        department_id = self.request.query_params.get("department_id")
        if user_id:
            qs = qs.filter(user_id=user_id)
        if did_id:
            qs = qs.filter(did_id=did_id)
        if department_id:
            qs = qs.filter(department_id=department_id)
        return qs.order_by("user__email", "did__number")


class UserDIDDepartmentAssignmentDetailView(generics.RetrieveDestroyAPIView):
    """
    GET/DELETE /api/v1/dids/department-assignments/{id}/
    """
    serializer_class = UserDIDDepartmentAssignmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = UserDIDDepartmentAssignment.objects.select_related("user", "did", "department")
    lookup_field = "id"


# ---------------------------------------------------------------------------
# AccessGroup — reusable log-visibility bundle
# ---------------------------------------------------------------------------

class AccessGroupListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/access-groups/  — list access groups with their entries.
    POST /api/v1/dids/access-groups/  — create an access group.
    """
    serializer_class = AccessGroupSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = AccessGroup.objects.prefetch_related("entries__did", "entries__department").order_by("name")


class AccessGroupDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/access-groups/{id}/
    """
    serializer_class = AccessGroupSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = AccessGroup.objects.prefetch_related("entries__did", "entries__department")
    lookup_field = "id"


class AccessGroupEntryListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/access-groups/{group_id}/entries/  — list entries in a group.
    POST /api/v1/dids/access-groups/{group_id}/entries/  — add a DID (+ optional Department) rule.
    """
    serializer_class = AccessGroupEntrySerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        return AccessGroupEntry.objects.filter(group_id=self.kwargs["group_id"]).select_related("did", "department")

    def perform_create(self, serializer):
        serializer.save(group_id=self.kwargs["group_id"])


class AccessGroupEntryDetailView(generics.RetrieveDestroyAPIView):
    """
    GET/DELETE /api/v1/dids/access-groups/{group_id}/entries/{id}/
    """
    serializer_class = AccessGroupEntrySerializer
    permission_classes = [IsAdminOrSuperAdmin]
    lookup_field = "id"

    def get_queryset(self):
        return AccessGroupEntry.objects.filter(group_id=self.kwargs["group_id"]).select_related("did", "department")


# ---------------------------------------------------------------------------
# TLGroupAccess — grants a TL visibility into an AccessGroup
# ---------------------------------------------------------------------------

class TLGroupAccessListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/tl-group-access/  — list TL access grants.
         Filter by ?user_id=, ?group_id=.
    POST /api/v1/dids/tl-group-access/  — grant a TL access to an AccessGroup.
    """
    serializer_class = TLGroupAccessSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        qs = TLGroupAccess.objects.select_related("user", "group")
        user_id = self.request.query_params.get("user_id")
        group_id = self.request.query_params.get("group_id")
        if user_id:
            qs = qs.filter(user_id=user_id)
        if group_id:
            qs = qs.filter(group_id=group_id)
        return qs.order_by("user__email")


class TLGroupAccessDetailView(generics.RetrieveDestroyAPIView):
    """
    GET/DELETE /api/v1/dids/tl-group-access/{id}/
    """
    serializer_class = TLGroupAccessSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = TLGroupAccess.objects.select_related("user", "group")
    lookup_field = "id"
