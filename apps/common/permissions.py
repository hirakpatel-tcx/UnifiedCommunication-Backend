"""
apps/common/permissions.py
──────────────────────────
Custom DRF permission classes for role-based access control (RBAC).

Roles:
- superadmin: Platform operator. Access across all tenants.
- admin: Tenant administrator. Access restricted to their own tenant.
- user: Standard end-user. Access restricted to their assigned resources.
"""

from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """
    Allows access only to superadmins / platform administrators.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, "role", "") == "superadmin"))


class IsAdminOrSuperAdmin(BasePermission):
    """
    Allows access to platform superadmins and tenant administrators.
    Regular users ('user' role) are denied.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(user.is_superuser or getattr(user, "role", "") in ("superadmin", "admin"))


class IsSupervisorOrAdmin(BasePermission):
    """
    Allows access to platform superadmins, tenant administrators, and team
    leads. Intended for call-monitoring actions (listen/whisper/barge) that
    should not be exposed to regular agents.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(
            user.is_superuser
            or getattr(user, "role", "") in ("superadmin", "admin")
            or getattr(user, "is_team_lead", False)
        )
