"""
apps/users/serializers.py
─────────────────────────
Serializers for authentication, user management, and unified resource provisioning.
"""

import uuid
from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers

from django.core.exceptions import ValidationError as DjangoValidationError
from apps.common.services.secret_service import SecretService
from apps.dids.models import DID, UserDID
from apps.extensions.models import Extension
from apps.tenants.models import Tenant
from apps.users.models import User, UserRole
from apps.users.validators import validate_fax_boxes, validate_voicemail_boxes


# ---------------------------------------------------------------------------
# Summary serializers for nested representations
# ---------------------------------------------------------------------------

class ExtensionSummarySerializer(serializers.ModelSerializer):
    sip_password = serializers.SerializerMethodField()

    class Meta:
        model = Extension
        fields = [
            "id",
            "extension_number",
            "sip_username",
            "sip_password",
            "transport_type",
        ]

    def get_sip_password(self, obj) -> str:
        if getattr(obj, "encrypted_sip_password", None):
            try:
                return SecretService.decrypt(obj.encrypted_sip_password)
            except Exception:
                return ""
        return ""
    

class UserDIDSummarySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="did.id")
    number = serializers.CharField(source="did.number")
    name = serializers.CharField(source="did.name", allow_blank=True, default="")
    did_name = serializers.CharField(source="did.name", allow_blank=True, default="")
    did_number = serializers.CharField(source="did.number", allow_blank=True, default="")

    class Meta:
        model = UserDID
        fields = ["id", "number", "name", "did_number", "did_name"]


class TenantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "freeswitch_tenant_uuid",
            "tenant_code",
            "tenant_name",
            "sip_domain",
        ]


class UserDetailSerializer(serializers.ModelSerializer):
    tenant = TenantSummarySerializer(read_only=True)
    extension = ExtensionSummarySerializer(read_only=True)
    dids = UserDIDSummarySerializer(source="user_dids", many=True, read_only=True)
    features = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "role",
            "is_active",
            "tenant",
            "features",
            "extension",
            "dids",
            "fax_boxes",
            "voicemail_boxes",
            "created_at",
        ]

    def get_features(self, obj) -> dict:
        if obj.tenant and hasattr(obj.tenant, "features"):
            return {
                k: v
                for k, v in obj.tenant.features.items()
                if k != "voicemail"
            }
        return {
            "calling": False,
            "messaging": False,
            "fax": False,
        }


# ---------------------------------------------------------------------------
# Authentication serializer
# ---------------------------------------------------------------------------

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        email = attrs.get("email", "").strip().lower()
        password = attrs.get("password")

        # 1. Check if user exists (with eager loading of tenant, extension, and user_dids)
        user = (
            User.objects.select_related("tenant", "extension")
            .prefetch_related("user_dids__did")
            .filter(email=email)
            .first()
        )
        if not user:
            raise serializers.ValidationError(
                {"detail": "No account with this email found."},
                code="authorization",
            )

        # 2. Check if password is correct
        if not user.check_password(password):
            raise serializers.ValidationError(
                {"detail": "Invalid password."},
                code="authorization",
            )

        # 3. Check if user is active
        if not user.is_active:
            raise serializers.ValidationError(
                {"detail": "This user account is disabled."},
                code="authorization",
            )

        # 4. Safeguards based on tenant features:
        tenant = user.tenant
        calling_enabled = bool(tenant and (tenant.features or {}).get("calling", False))
        messaging_enabled = bool(tenant and (tenant.features or {}).get("messaging", False))

        assigned_dids = list(user.user_dids.all())
        has_did = len(assigned_dids) > 0

        # Scenario 1: Calling is enabled -> both Extension and DID are required
        if calling_enabled:
            has_extension = hasattr(user, "extension") and user.extension is not None

            if not has_extension and not has_did:
                raise serializers.ValidationError(
                    {"detail": "Calling is enabled, but you do not have an extension or a DID assigned. Please contact your administrator."},
                    code="calling_resources_missing",
                )
            if not has_extension:
                raise serializers.ValidationError(
                    {"detail": "Calling is enabled, but you do not have an extension assigned. Please contact your administrator."},
                    code="extension_missing",
                )
            if not has_did:
                raise serializers.ValidationError(
                    {"detail": "Calling is enabled, but you do not have a DID assigned. Please contact your administrator."},
                    code="did_missing",
                )

        # Scenario 2: Messaging is enabled and Calling is disabled -> only DIDs allowed/required
        elif messaging_enabled:
            if not has_did:
                raise serializers.ValidationError(
                    {"detail": "Messaging is enabled, but you do not have a DID assigned. Please contact your administrator."},
                    code="messaging_did_missing",
                )

        attrs["user"] = user
        return attrs


# ---------------------------------------------------------------------------
# Unified User Provisioning & Update Serializer (One API for all)
# ---------------------------------------------------------------------------

def _resolve_tenant(ref):
    if not ref:
        return None
    raw = str(ref).strip()
    try:
        val_uuid = uuid.UUID(raw)
        t = Tenant.objects.filter(Q(id=val_uuid) | Q(freeswitch_tenant_uuid=val_uuid)).first()
        if t:
            return t
    except (ValueError, AttributeError):
        pass
    return Tenant.objects.filter(tenant_code__iexact=raw).first()


def _resolve_extension(ref, tenant):
    if not ref or not tenant:
        return None
    raw = str(ref).strip()
    try:
        val_uuid = uuid.UUID(raw)
        ext = Extension.objects.filter(tenant=tenant).filter(Q(id=val_uuid) | Q(freeswitch_object_id=raw)).first()
        if ext:
            return ext
    except (ValueError, AttributeError):
        pass
    return Extension.objects.filter(tenant=tenant, extension_number=raw).first()


def _resolve_did(ref, tenant):
    if not ref or not tenant:
        return None
    raw = str(ref).strip()
    try:
        val_uuid = uuid.UUID(raw)
        d = DID.objects.filter(tenant=tenant).filter(Q(id=val_uuid) | Q(freeswitch_object_id=raw)).first()
        if d:
            return d
    except (ValueError, AttributeError):
        pass
    return DID.objects.filter(tenant=tenant, number=raw).first()


class UserUpsertSerializer(serializers.ModelSerializer):
    """
    Unified serializer for creating and updating users with atomic resource assignment:
    - User profile (email, password, role, is_active)
    - Extension assignment (by UUID or extension number)
    - DID assignments (by UUID or phone number)
    - Fax boxes
    - Voicemail boxes
    """
    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})
    tenant_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    sip_domain = serializers.CharField(required=False, allow_blank=True, default="")
    extension_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    did_ids = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    fax_boxes = serializers.ListField(child=serializers.DictField(), required=False, allow_empty=True)
    voicemail_boxes = serializers.ListField(child=serializers.IntegerField(min_value=0), required=False, allow_empty=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "password",
            "role",
            "tenant_id",
            "sip_domain",
            "is_active",
            "extension_id",
            "did_ids",
            "fax_boxes",
            "voicemail_boxes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        # Email validation on create
        if not self.instance:
            if not attrs.get("email"):
                raise serializers.ValidationError({"email": "Email is required when creating a user."})
            if not attrs.get("password"):
                raise serializers.ValidationError({"password": "Password is required when creating a user."})
            email = attrs["email"].strip().lower()
            if User.objects.filter(email=email).exists():
                raise serializers.ValidationError({"email": "A user with this email already exists."})
        elif "email" in attrs:
            email = attrs["email"].strip().lower()
            if User.objects.filter(email=email).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError({"email": "A user with this email already exists."})

        # Validate fax_boxes if supplied
        if "fax_boxes" in attrs and attrs["fax_boxes"] is not None:
            try:
                validate_fax_boxes(attrs["fax_boxes"])
            except DjangoValidationError as e:
                raise serializers.ValidationError({"fax_boxes": e.messages})

        # Validate voicemail_boxes if supplied
        if "voicemail_boxes" in attrs and attrs["voicemail_boxes"] is not None:
            try:
                validate_voicemail_boxes(attrs["voicemail_boxes"])
            except DjangoValidationError as e:
                raise serializers.ValidationError({"voicemail_boxes": e.messages})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        caller = request.user if request else None

        extension_ref = validated_data.pop("extension_id", None)
        did_refs = validated_data.pop("did_ids", None)
        raw_password = validated_data.pop("password")
        raw_tenant = validated_data.pop("tenant_id", None)

        # Resolve tenant
        tenant = None
        if caller and (caller.is_superuser or getattr(caller, "role", "") == "superadmin"):
            if raw_tenant:
                tenant = _resolve_tenant(raw_tenant)
                if not tenant:
                    raise serializers.ValidationError({"tenant_id": f"Tenant '{raw_tenant}' not found."})
            else:
                # If extension_id was provided for an existing extension, infer tenant from it
                if extension_ref:
                    raw_ext = str(extension_ref).strip()
                    try:
                        val_uuid = uuid.UUID(raw_ext)
                        candidate_ext = Extension.objects.filter(Q(id=val_uuid) | Q(freeswitch_object_id=raw_ext)).first()
                        if candidate_ext:
                            tenant = candidate_ext.tenant
                    except (ValueError, AttributeError):
                        pass

                # If still no tenant and role is not superadmin, require tenant_id
                role = validated_data.get("role", UserRole.USER)
                if not tenant and role != UserRole.SUPERADMIN:
                    raise serializers.ValidationError({"tenant_id": "tenant_id is required when creating a tenant user."})
        elif caller and caller.tenant:
            tenant = caller.tenant
        elif raw_tenant:
            tenant = _resolve_tenant(raw_tenant)

        # Validate feature flags
        fax_boxes = validated_data.get("fax_boxes")
        if fax_boxes and not (tenant and (tenant.features or {}).get("fax", False)):
            raise serializers.ValidationError({"fax_boxes": "Fax feature is disabled for this tenant. Cannot assign fax boxes."})

        # Create user
        user = User.objects.create_user(
            password=raw_password,
            tenant=tenant,
            **validated_data
        )

        # Handle Extension assignment
        if extension_ref is not None:
            if extension_ref in ("", "null", None):
                pass
            else:
                if not tenant:
                    raise serializers.ValidationError({"extension_id": "Cannot assign extension without a tenant."})
                if not (tenant.features or {}).get("calling", False):
                    raise serializers.ValidationError({"extension_id": "Calling feature is disabled for this tenant. Cannot assign extension."})
                ext = _resolve_extension(extension_ref, tenant)
                if not ext:
                    raise serializers.ValidationError({"extension_id": f"Extension '{extension_ref}' not found in tenant."})
                Extension.objects.filter(user=user).update(user=None)
                Extension.objects.filter(id=ext.id).update(user=user)

        # Handle DIDs assignment
        if did_refs is not None:
            if not tenant and did_refs:
                raise serializers.ValidationError({"did_ids": "Cannot assign DIDs without a tenant."})
            calling_enabled = bool(tenant and (tenant.features or {}).get("calling", False))
            messaging_enabled = bool(tenant and (tenant.features or {}).get("messaging", False))
            if did_refs and not calling_enabled and not messaging_enabled:
                raise serializers.ValidationError({"did_ids": "Both calling and messaging features are disabled for this tenant. Cannot assign DIDs."})
            for did_ref in did_refs:
                did = _resolve_did(did_ref, tenant)
                if not did:
                    raise serializers.ValidationError({"did_ids": f"DID '{did_ref}' not found in tenant."})
                UserDID.objects.get_or_create(user=user, did=did)

        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        has_ext = "extension_id" in validated_data
        extension_ref = validated_data.pop("extension_id", None)

        has_dids = "did_ids" in validated_data
        did_refs = validated_data.pop("did_ids", None)

        raw_password = validated_data.pop("password", None)
        raw_tenant = validated_data.pop("tenant_id", None)

        if raw_password:
            instance.set_password(raw_password)

        if raw_tenant is not None:
            request = self.context.get("request")
            caller = request.user if request else None
            if caller and (caller.is_superuser or getattr(caller, "role", "") == "superadmin"):
                instance.tenant = _resolve_tenant(raw_tenant)

        tenant = instance.tenant

        # Validate feature flags
        if "fax_boxes" in validated_data and validated_data["fax_boxes"]:
            if not (tenant and (tenant.features or {}).get("fax", False)):
                raise serializers.ValidationError({"fax_boxes": "Fax feature is disabled for this tenant. Cannot assign fax boxes."})

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Handle extension update if passed
        if has_ext:
            if extension_ref in (None, "", "null"):
                Extension.objects.filter(user=instance).update(user=None)
            else:
                if not tenant:
                    raise serializers.ValidationError({"extension_id": "User has no tenant assigned."})
                if not (tenant.features or {}).get("calling", False):
                    raise serializers.ValidationError({"extension_id": "Calling feature is disabled for this tenant. Cannot assign extension."})
                ext = _resolve_extension(extension_ref, tenant)
                if not ext:
                    raise serializers.ValidationError({"extension_id": f"Extension '{extension_ref}' not found in tenant."})
                Extension.objects.filter(user=instance).update(user=None)
                Extension.objects.filter(id=ext.id).update(user=instance)

        # Handle DIDs update if passed
        if has_dids:
            if not did_refs:
                # Clear all assigned DIDs
                UserDID.objects.filter(user=instance).delete()
            else:
                if not tenant:
                    raise serializers.ValidationError({"did_ids": "User has no tenant assigned."})
                calling_enabled = bool(tenant and (tenant.features or {}).get("calling", False))
                messaging_enabled = bool(tenant and (tenant.features or {}).get("messaging", False))
                if not calling_enabled and not messaging_enabled:
                    raise serializers.ValidationError({"did_ids": "Both calling and messaging features are disabled for this tenant. Cannot assign DIDs."})
                target_dids = []
                for did_ref in did_refs:
                    did = _resolve_did(did_ref, tenant)
                    if not did:
                        raise serializers.ValidationError({"did_ids": f"DID '{did_ref}' not found in tenant."})
                    target_dids.append(did)

                # Sync: remove unlisted, add new
                UserDID.objects.filter(user=instance).exclude(did__in=target_dids).delete()
                for d in target_dids:
                    UserDID.objects.get_or_create(user=instance, did=d)

        return instance


# ---------------------------------------------------------------------------
# Granular Micro-Assignment Serializers (retained for backward compatibility)
# ---------------------------------------------------------------------------

class ExtensionAssignSerializer(serializers.Serializer):
    extension_id = serializers.CharField(required=True)


class DIDAssignSerializer(serializers.Serializer):
    did_id = serializers.CharField(required=True)


class FaxBoxAssignSerializer(serializers.Serializer):
    fax_uuid = serializers.CharField(required=True)
    fax_caller_id_name = serializers.CharField(required=False, default="", allow_blank=True)
    fax_caller_id_number = serializers.CharField(required=True)


class VoicemailBoxAssignSerializer(serializers.Serializer):
    voicemail_box_id = serializers.IntegerField(required=True, min_value=0)
