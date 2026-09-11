"""
apps/dids/serializers.py
────────────────────────
Serializers for DIDs.
"""

from rest_framework import serializers
from apps.dids.models import (
    DID,
    UserDID,
    Department,
    UserDIDDepartmentAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)


class AssignedUserSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(source="user.id")
    email = serializers.EmailField(source="user.email")


class DIDSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.tenant_code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.tenant_name", read_only=True)
    assigned_users = AssignedUserSummarySerializer(source="user_dids", many=True, read_only=True)
    assigned_users_count = serializers.IntegerField(source="user_dids.count", read_only=True)
    did_name = serializers.CharField(source="name", read_only=True)
    did_number = serializers.CharField(source="number", read_only=True)

    class Meta:
        model = DID
        fields = [
            "id",
            "tenant_id",
            "tenant_code",
            "tenant_name",
            "freeswitch_object_id",
            "number",
            "did_number",
            "did_name",
            "assigned_users_count",
            "assigned_users",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_code",
            "tenant_name",
            "assigned_users_count",
            "assigned_users",
            "created_at",
            "updated_at",
        ]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class UserDIDDepartmentAssignmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    did_number = serializers.CharField(source="did.number", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = UserDIDDepartmentAssignment
        fields = [
            "id",
            "user",
            "user_email",
            "did",
            "did_number",
            "department",
            "department_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user_email", "did_number", "department_name", "created_at", "updated_at"]


class AccessGroupEntrySerializer(serializers.ModelSerializer):
    did_number = serializers.CharField(source="did.number", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = AccessGroupEntry
        fields = [
            "id",
            "group",
            "did",
            "did_number",
            "department",
            "department_name",
            "created_at",
        ]
        read_only_fields = ["id", "group", "did_number", "department_name", "created_at"]


class AccessGroupSerializer(serializers.ModelSerializer):
    entries = AccessGroupEntrySerializer(many=True, read_only=True)

    class Meta:
        model = AccessGroup
        fields = ["id", "name", "description", "entries", "created_at", "updated_at"]
        read_only_fields = ["id", "entries", "created_at", "updated_at"]


class TLGroupAccessSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = TLGroupAccess
        fields = ["id", "user", "user_email", "group", "group_name", "created_at"]
        read_only_fields = ["id", "user_email", "group_name", "created_at"]

    def validate_user(self, user):
        if not user.is_team_lead:
            raise serializers.ValidationError(
                "This user is not marked as a Team Lead (is_team_lead=False). "
                "Enable Team Lead on the user before granting log access."
            )
        return user
