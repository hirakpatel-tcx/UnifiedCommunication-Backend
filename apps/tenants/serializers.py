"""
apps/tenants/serializers.py
───────────────────────────
Serializers for Tenant management.
"""

from rest_framework import serializers
from apps.common.services.secret_service import SecretService
from apps.tenants.models import Tenant


class TenantSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    extensions_count = serializers.IntegerField(source="extensions.count", read_only=True)
    dids_count = serializers.IntegerField(source="dids.count", read_only=True)
    users_count = serializers.IntegerField(source="users.count", read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id",
            "freeswitch_tenant_uuid",
            "tenant_code",
            "tenant_name",
            "sip_domain",
            "api_key",
            "features",
            "is_active",
            "extensions_count",
            "dids_count",
            "users_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "extensions_count",
            "dids_count",
            "users_count",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        raw_api_key = validated_data.pop("api_key", None)
        if raw_api_key:
            validated_data["encrypted_api_key"] = SecretService.encrypt(raw_api_key)
        else:
            validated_data["encrypted_api_key"] = ""
        return super().create(validated_data)

    def update(self, instance, validated_data):
        raw_api_key = validated_data.pop("api_key", None)
        if raw_api_key:
            validated_data["encrypted_api_key"] = SecretService.encrypt(raw_api_key)
        return super().update(instance, validated_data)
