"""
apps/contacts/permissions.py
────────────────────────────
Permission classes for Contact access control.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS
from apps.contacts.models import DirectoryType


class IsContactOwnerOrCompanyAdmin(BasePermission):
    """
    Access rules for Contacts:
    - Company Directory contacts:
      - Read: Any authenticated user in the same tenant.
      - Write/Delete: Only tenant Admins or SuperAdmins.
    - Personal Directory contacts:
      - Read/Write/Delete: Only the owner of the contact (or SuperAdmin).
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser:
            return True

        # Cross-tenant isolation
        if obj.tenant_id != user.tenant_id:
            return False

        is_admin = getattr(user, "role", "") in ("admin", "superadmin")

        if obj.directory_type == DirectoryType.COMPANY:
            if request.method in SAFE_METHODS:
                return True
            return is_admin

        # Personal contacts
        if obj.directory_type == DirectoryType.PERSONAL:
            return obj.owner_id == user.id

        return False
