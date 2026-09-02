"""
apps/extensions/serializers.py
──────────────────────────────
Serializers for FreeSWITCH Extensions.
"""

from rest_framework import serializers
from apps.extensions.models import Extension, TransportType


class ExtensionTransportUpdateSerializer(serializers.Serializer):
    transport_type = serializers.CharField(required=True)

    def validate_transport_type(self, value):
        val = str(value).upper().strip()
        valid_choices = [c[0] for c in TransportType.choices]
        if val not in valid_choices:
            raise serializers.ValidationError(
                f"Invalid transport_type '{value}'. Valid choices: {', '.join(valid_choices)}."
            )
        return val


class ExtensionSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.tenant_code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.tenant_name", read_only=True)
    assigned_user_id = serializers.UUIDField(source="user.id", read_only=True, allow_null=True)
    assigned_user_email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)

    class Meta:
        model = Extension
        fields = [
            "id",
            "tenant_id",
            "tenant_code",
            "tenant_name",
            "freeswitch_object_id",
            "extension_number",
            "sip_username",
            "transport_type",
            "assigned_user_id",
            "assigned_user_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "tenant_code",
            "tenant_name",
            "freeswitch_object_id",
            "extension_number",
            "sip_username",
            "assigned_user_id",
            "assigned_user_email",
            "created_at",
            "updated_at",
        ]

    def validate_transport_type(self, value):
        val = str(value).upper().strip()
        valid_choices = [c[0] for c in TransportType.choices]
        if val not in valid_choices:
            raise serializers.ValidationError(
                f"Invalid transport_type '{value}'. Valid choices: {', '.join(valid_choices)}."
            )
        return val
