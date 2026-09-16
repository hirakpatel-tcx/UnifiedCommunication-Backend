"""
apps/dids/views.py
──────────────────
REST API views for DID listing and details.
"""

from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.permissions import IsAdminOrSuperAdmin
from apps.common.tenant_resolver import get_scoped_tenant
from apps.common.tl_scoping import resolve_tl_did_ids, resolve_tl_department_ids
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

        # Team Leads are restricted to DIDs named in their granted
        # AccessGroup entries (TLGroupAccess). Grants resolving to zero DIDs
        # correctly yield an empty listing rather than the tenant-wide list.
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", "") == "superadmin"):
            tl_did_ids = resolve_tl_did_ids(user)
            if tl_did_ids is not None:
                qs = qs.filter(id__in=tl_did_ids)

        # Search query (by phone number)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(number__icontains=search)

        # Department filter (multi-select): ?department_id=1,2,3
        # A DID matches if any caller works it under that department.
        department_id = self.request.query_params.get("department_id")
        if department_id:
            department_ids = [d.strip() for d in department_id.split(",") if d.strip()]
            if department_ids:
                qs = qs.filter(
                    user_department_assignments__department_id__in=department_ids
                ).distinct()

        # User filter (multi-select): ?user_id=1,2,3
        # A DID matches if it's assigned to any of these users.
        user_id = self.request.query_params.get("user_id")
        if user_id:
            user_ids = [u.strip() for u in user_id.split(",") if u.strip()]
            if user_ids:
                qs = qs.filter(user_dids__user_id__in=user_ids).distinct()

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

    def get_queryset(self):
        qs = Department.objects.all().order_by("name")
        user = self.request.user
        if self.request.method == "GET" and not (
            user.is_superuser or getattr(user, "role", "") == "superadmin"
        ):
            tl_department_ids = resolve_tl_department_ids(user)
            if tl_department_ids is not None:
                qs = qs.filter(id__in=tl_department_ids)

        # DID filter (multi-select): ?did_id=1,2,3
        # A department matches if any caller works that DID under it.
        did_id = self.request.query_params.get("did_id")
        if did_id:
            did_ids = [d.strip() for d in did_id.split(",") if d.strip()]
            if did_ids:
                qs = qs.filter(user_did_assignments__did_id__in=did_ids).distinct()

        # User filter (multi-select): ?user_id=1,2,3
        # A department matches if that user works any DID under it.
        user_id = self.request.query_params.get("user_id")
        if user_id:
            user_ids = [u.strip() for u in user_id.split(",") if u.strip()]
            if user_ids:
                qs = qs.filter(user_did_assignments__user_id__in=user_ids).distinct()

        return qs


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


# ---------------------------------------------------------------------------
# MyLogAccessRoster — the requesting TL's own roster, for the logs section
# ---------------------------------------------------------------------------

class MyLogAccessRosterView(APIView):
    """
    GET /api/v1/dids/my-log-access-roster/

    Resolves the requesting user's own TLGroupAccess grants into a roster:
    one row per (caller, DID, Department) they are permitted to see call
    logs for, with the caller's extension number attached. This is the
    "who am I looking at" view for the TL Logs section — a companion to
    the CDR endpoints, which use the same underlying resolution to scope
    the actual call records (see apps.common.cdr_views._resolve_tl_extensions).

    Every authenticated user can call this — it only ever returns rows
    derived from their own grants, and returns an empty list for a user
    with no TLGroupAccess grants at all.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        group_ids = TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
        entries = AccessGroupEntry.objects.filter(group_id__in=group_ids).select_related("did", "department")

        roster = []
        seen = set()
        for entry in entries:
            qs = UserDIDDepartmentAssignment.objects.filter(did_id=entry.did_id).select_related(
                "user__extension", "did", "department"
            )
            if entry.department_id is not None:
                qs = qs.filter(department_id=entry.department_id)

            for assignment in qs:
                caller = assignment.user
                ext = getattr(caller, "extension", None)
                key = (caller.id, assignment.did_id, assignment.department_id)
                if key in seen:
                    continue
                seen.add(key)
                roster.append({
                    "user_id": caller.id,
                    "user_email": caller.email,
                    "first_name": caller.first_name,
                    "last_name": caller.last_name,
                    "extension_number": ext.extension_number if ext else None,
                    "did": assignment.did_id,
                    "did_number": assignment.did.number,
                    "department": assignment.department_id,
                    "department_name": assignment.department.name,
                })

        roster.sort(key=lambda row: (row["department_name"], row["did_number"], row["user_email"]))
        return Response(roster)
