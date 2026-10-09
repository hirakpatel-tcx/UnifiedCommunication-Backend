"""
apps/dids/views.py
──────────────────
REST API views for DID listing and details.
"""

from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.permissions import IsAdminOrSuperAdmin, IsSupervisorOrAdmin
from apps.common.tenant_resolver import get_scoped_tenant
from apps.common.tl_scoping import (
    resolve_tl_did_ids,
    resolve_tl_department_ids,
    resolve_tl_division_ids,
)
from apps.dids.models import (
    DID,
    Department,
    Division,
    Account,
    UserDIDDivisionAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)
from apps.dids.serializers import (
    DIDSerializer,
    DepartmentSerializer,
    DivisionSerializer,
    AccountSerializer,
    UserDIDDivisionAssignmentSerializer,
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
    permission_classes = [IsSupervisorOrAdmin]

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        qs = (
            DID.objects.filter(tenant=tenant)
            .select_related("tenant", "department", "account")
            .prefetch_related("user_dids__user")
        )

        # Team Leads are restricted to DIDs named in their granted
        # AccessGroup entries (TLGroupAccess), including DIDs reached via a
        # department-scoped grant. Grants resolving to zero DIDs correctly
        # yield an empty listing rather than the tenant-wide list.
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
        department_id = self.request.query_params.get("department_id")
        if department_id:
            department_ids = [d.strip() for d in department_id.split(",") if d.strip()]
            if department_ids:
                qs = qs.filter(department_id__in=department_ids)

        # Account filter (multi-select): ?account_id=1,2,3
        account_id = self.request.query_params.get("account_id")
        if account_id:
            account_ids = [a.strip() for a in account_id.split(",") if a.strip()]
            if account_ids:
                qs = qs.filter(account_id__in=account_ids)

        # Division filter (multi-select): ?division_id=1,2,3
        # A DID matches if any caller works it under that division.
        division_id = self.request.query_params.get("division_id")
        if division_id:
            division_ids = [t.strip() for t in division_id.split(",") if t.strip()]
            if division_ids:
                qs = qs.filter(
                    user_division_assignments__division_id__in=division_ids
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
    """
    serializer_class = DIDSerializer
    permission_classes = [IsSupervisorOrAdmin]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        qs = DID.objects.select_related("tenant", "department", "account").prefetch_related("user_dids__user").all()
        if user.is_superuser or user.role == "superadmin":
            return qs
        qs = qs.filter(tenant_id=user.tenant_id) if user.tenant_id else qs.none()
        tl_did_ids = resolve_tl_did_ids(user)
        if tl_did_ids is not None:
            qs = qs.filter(id__in=tl_did_ids)
        return qs


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class DepartmentListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/departments/  — list departments scoped to a tenant.
    POST /api/v1/dids/departments/  — create a department.
    For superadmin: 'tenant_id' query parameter or 'X-Tenant-ID' header is required.
    For admin: automatically scoped to the user's tenant.
    """
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsSupervisorOrAdmin()]
        return [IsAdminOrSuperAdmin()]

    def get_queryset(self):
        tenant = get_scoped_tenant(self.request)
        qs = Department.objects.filter(tenant=tenant).select_related("tenant")

        user = self.request.user
        if self.request.method == "GET" and not (
            user.is_superuser or getattr(user, "role", "") == "superadmin"
        ):
            tl_department_ids = resolve_tl_department_ids(user)
            if tl_department_ids is not None:
                qs = qs.filter(id__in=tl_department_ids)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)

        return qs


class DepartmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/departments/{id}/
    """
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        qs = Department.objects.select_related("tenant").all()
        if user.is_superuser or user.role == "superadmin":
            return qs
        if user.tenant_id:
            return qs.filter(tenant_id=user.tenant_id)
        return qs.none()


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

class AccountListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/accounts/  — list all accounts (global, not tenant-scoped).
    POST /api/v1/dids/accounts/  — create an account.
    """
    serializer_class = AccountSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        qs = Account.objects.all().order_by("name")

        # DID filter (multi-select): ?did_id=1,2,3
        did_id = self.request.query_params.get("did_id")
        if did_id:
            did_ids = [d.strip() for d in did_id.split(",") if d.strip()]
            if did_ids:
                qs = qs.filter(dids__id__in=did_ids).distinct()

        return qs


class AccountDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/accounts/{id}/
    """
    serializer_class = AccountSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = Account.objects.all()
    lookup_field = "id"


# ---------------------------------------------------------------------------
# Division
# ---------------------------------------------------------------------------

class DivisionListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/divisions/  — list all divisions (global, not tenant-scoped).
    POST /api/v1/dids/divisions/  — create a division.
    """
    serializer_class = DivisionSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        qs = Division.objects.all().order_by("name")
        user = self.request.user
        if self.request.method == "GET" and not (
            user.is_superuser or getattr(user, "role", "") == "superadmin"
        ):
            tl_division_ids = resolve_tl_division_ids(user)
            if tl_division_ids is not None:
                qs = qs.filter(id__in=tl_division_ids)

        # DID filter (multi-select): ?did_id=1,2,3
        # A division matches if any caller works that DID under it.
        did_id = self.request.query_params.get("did_id")
        if did_id:
            did_ids = [d.strip() for d in did_id.split(",") if d.strip()]
            if did_ids:
                qs = qs.filter(user_did_assignments__did_id__in=did_ids).distinct()

        # User filter (multi-select): ?user_id=1,2,3
        # A division matches if that user works any DID under it.
        user_id = self.request.query_params.get("user_id")
        if user_id:
            user_ids = [u.strip() for u in user_id.split(",") if u.strip()]
            if user_ids:
                qs = qs.filter(user_did_assignments__user_id__in=user_ids).distinct()

        return qs


class DivisionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/divisions/{id}/
    """
    serializer_class = DivisionSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = Division.objects.all()
    lookup_field = "id"


# ---------------------------------------------------------------------------
# UserDIDDivisionAssignment — caller work assignment
# ---------------------------------------------------------------------------

class UserDIDDivisionAssignmentListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/division-assignments/  — list assignments.
         Filter by ?user_id=, ?did_id=, ?division_id=.
    POST /api/v1/dids/division-assignments/  — assign a caller to a DID + Division.
    """
    serializer_class = UserDIDDivisionAssignmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        qs = UserDIDDivisionAssignment.objects.select_related("user", "did", "division")
        user_id = self.request.query_params.get("user_id")
        did_id = self.request.query_params.get("did_id")
        division_id = self.request.query_params.get("division_id")
        if user_id:
            qs = qs.filter(user_id=user_id)
        if did_id:
            qs = qs.filter(did_id=did_id)
        if division_id:
            qs = qs.filter(division_id=division_id)
        return qs.order_by("user__email", "did__number")


class UserDIDDivisionAssignmentDetailView(generics.RetrieveDestroyAPIView):
    """
    GET/DELETE /api/v1/dids/division-assignments/{id}/
    """
    serializer_class = UserDIDDivisionAssignmentSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = UserDIDDivisionAssignment.objects.select_related("user", "did", "division")
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
    queryset = AccessGroup.objects.prefetch_related(
        "entries__did", "entries__department", "entries__division"
    ).order_by("name")


class AccessGroupDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /api/v1/dids/access-groups/{id}/
    """
    serializer_class = AccessGroupSerializer
    permission_classes = [IsAdminOrSuperAdmin]
    queryset = AccessGroup.objects.prefetch_related(
        "entries__did", "entries__department", "entries__division"
    )
    lookup_field = "id"


class AccessGroupEntryListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/dids/access-groups/{group_id}/entries/  — list entries in a group.
    POST /api/v1/dids/access-groups/{group_id}/entries/  — add a rule granting
         either a single DID or a whole Department (+ optional Division).
    """
    serializer_class = AccessGroupEntrySerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        return AccessGroupEntry.objects.filter(group_id=self.kwargs["group_id"]).select_related(
            "did", "department", "division"
        )

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
        return AccessGroupEntry.objects.filter(group_id=self.kwargs["group_id"]).select_related(
            "did", "department", "division"
        )


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
    one row per (caller, DID, Division) they are permitted to see call logs
    for, with the caller's extension number attached. This is the "who am
    I looking at" view for the TL Logs section — a companion to the CDR
    endpoints, which use the same underlying resolution to scope the
    actual call records (see apps.common.cdr_views._resolve_tl_extensions).

    Every authenticated user can call this — it only ever returns rows
    derived from their own grants, and returns an empty list for a user
    with no TLGroupAccess grants at all.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        group_ids = TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
        entries = AccessGroupEntry.objects.filter(group_id__in=group_ids).select_related(
            "did", "department", "division"
        )

        roster = []
        seen = set()
        for entry in entries:
            if entry.did_id is not None:
                did_ids = [entry.did_id]
            else:
                did_ids = list(DID.objects.filter(department_id=entry.department_id).values_list("id", flat=True))
            if not did_ids:
                continue

            qs = UserDIDDivisionAssignment.objects.filter(did_id__in=did_ids).select_related(
                "user__extension", "did", "division"
            )
            if entry.division_id is not None:
                qs = qs.filter(division_id=entry.division_id)

            for assignment in qs:
                caller = assignment.user
                ext = getattr(caller, "extension", None)
                key = (caller.id, assignment.did_id, assignment.division_id)
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
                    "division": assignment.division_id,
                    "division_name": assignment.division.name,
                })

        roster.sort(key=lambda row: (row["division_name"], row["did_number"], row["user_email"]))
        return Response(roster)
