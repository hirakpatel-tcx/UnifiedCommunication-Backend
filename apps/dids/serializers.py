"""
apps/dids/serializers.py
────────────────────────
Serializers for DIDs.
"""

from rest_framework import serializers
from apps.dids.models import (
    DID,
    Department,
    UserDID,
    Division,
    Account,
    UserDIDDivisionAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)


class AssignedUserSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(source="user.id")
    email = serializers.EmailField(source="user.email")


class DepartmentSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.tenant_code", read_only=True)
    did_count = serializers.IntegerField(source="dids.count", read_only=True)

    class Meta:
        model = Department
        fields = ["id", "tenant", "tenant_code", "name", "code", "did_count", "created_at", "updated_at"]
        read_only_fields = ["id", "tenant_code", "did_count", "created_at", "updated_at"]


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class DIDSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.tenant_code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.tenant_name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)
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
            "department",
            "department_name",
            "account",
            "account_name",
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
            "department_name",
            "account_name",
            "assigned_users_count",
            "assigned_users",
            "created_at",
            "updated_at",
        ]

    def validate_department(self, department):
        if department is None:
            return department
        did = self.instance
        if did is not None and department.tenant_id != did.tenant_id:
            raise serializers.ValidationError("Department must belong to the same tenant as the DID.")
        return department


class DivisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Division
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class UserDIDDivisionAssignmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    did_number = serializers.CharField(source="did.number", read_only=True)
    division_name = serializers.CharField(source="division.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = UserDIDDivisionAssignment
        fields = [
            "id",
            "user",
            "user_email",
            "did",
            "did_number",
            "division",
            "division_name",
            "department",
            "department_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user_email",
            "did_number",
            "division_name",
            "department_name",
            "created_at",
            "updated_at",
        ]


class AccessGroupEntrySerializer(serializers.ModelSerializer):
    did_number = serializers.CharField(source="did.number", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    division_name = serializers.CharField(source="division.name", read_only=True, default=None)

    class Meta:
        model = AccessGroupEntry
        fields = [
            "id",
            "group",
            "did",
            "did_number",
            "department",
            "department_name",
            "division",
            "division_name",
            "created_at",
        ]
        read_only_fields = ["id", "group", "did_number", "department_name", "division_name", "created_at"]

    def validate(self, attrs):
        did = attrs.get("did", getattr(self.instance, "did", None))
        department = attrs.get("department", getattr(self.instance, "department", None))
        if bool(did) == bool(department):
            raise serializers.ValidationError(
                "Exactly one of 'did' or 'department' must be set (not both, not neither)."
            )
        return attrs


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
