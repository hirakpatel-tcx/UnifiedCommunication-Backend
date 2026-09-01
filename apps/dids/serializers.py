"""
apps/dids/serializers.py
────────────────────────
Serializers for DIDs.
"""

from rest_framework import serializers
from apps.dids.models import DID, UserDID


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
            "calling_enabled",
            "messaging_enabled",
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
