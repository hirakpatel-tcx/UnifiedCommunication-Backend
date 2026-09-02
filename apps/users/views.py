"""
apps/users/views.py
───────────────────
Authentication, user management, and telephony resource assignment views.
"""

from django.contrib.auth.models import update_last_login
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from apps.common.permissions import IsAdminOrSuperAdmin, IsSuperAdmin
from apps.common.services.secret_service import SecretService
from apps.dids.models import DID, UserDID
from apps.extensions.models import Extension
from apps.users.models import User
from apps.users.serializers import (
    DIDAssignSerializer,
    ExtensionAssignSerializer,
    FaxBoxAssignSerializer,
    LoginSerializer,
    UserDetailSerializer,
    UserUpsertSerializer,
    VoicemailBoxAssignSerializer,
)


class LoginView(APIView):
    """
    POST /api/v1/auth/login/
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        update_last_login(None, user)

        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        refresh["tenant_id"] = str(user.tenant_id) if user.tenant_id else None

        user_data = UserDetailSerializer(user).data

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": user_data,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/
    Blacklists the provided refresh token to invalidate the user's session.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": "The 'refresh' token field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(
                {"detail": "Successfully logged out."},
                status=status.HTTP_200_OK,
            )
        except TokenError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class CurrentUserView(APIView):
    """
    GET /api/v1/auth/me/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = UserDetailSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class UserListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/users/ — List users with tenant filtering.
    POST /api/v1/users/ — Create user + extension + DIDs + fax + voicemail in one atomic API call.
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserUpsertSerializer
        return UserDetailSerializer

    def get_queryset(self):
        user = self.request.user
        qs = User.objects.select_related("tenant", "extension").prefetch_related("user_dids__did").all()

        # Role filter
        role = self.request.query_params.get("role")
        if role:
            qs = qs.filter(role=role)

        # Active filter
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            if is_active.lower() in ("true", "1"):
                qs = qs.filter(is_active=True)
            elif is_active.lower() in ("false", "0"):
                qs = qs.filter(is_active=False)

        # Tenant filter
        tenant_id = self.request.query_params.get("tenant_id")
        if user.is_superuser or user.role == "superadmin":
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
        else:
            if user.tenant_id:
                qs = qs.filter(tenant_id=user.tenant_id)
            else:
                qs = qs.none()

        return qs.order_by("email")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        fresh_user = User.objects.select_related("tenant", "extension").prefetch_related("user_dids__did").get(id=user.id)
        user_data = UserDetailSerializer(fresh_user).data
        return Response(user_data, status=status.HTTP_201_CREATED)


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/users/{id}/ — Retrieve full user profile with all resources.
    PATCH  /api/v1/users/{id}/ — Update user + extension + DIDs + fax + voicemail in one atomic call.
    DELETE /api/v1/users/{id}/ — Delete user.
    """
    permission_classes = [IsAdminOrSuperAdmin]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return UserUpsertSerializer
        return UserDetailSerializer

    def get_queryset(self):
        user = self.request.user
        qs = User.objects.select_related("tenant", "extension").prefetch_related("user_dids__did").all()
        if user.is_superuser or user.role == "superadmin":
            return qs
        if user.tenant_id:
            return qs.filter(tenant_id=user.tenant_id)
        return qs.none()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        fresh_user = User.objects.select_related("tenant", "extension").prefetch_related("user_dids__did").get(id=user.id)
        user_data = UserDetailSerializer(fresh_user).data
        return Response(user_data, status=status.HTTP_200_OK)


class SipCredentialsView(APIView):
    """
    GET /api/v1/users/{id}/sip-credentials/
    Returns decrypted SIP credentials for softphone client registration.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)

        # Authorization: caller must be target user, tenant admin, or superadmin
        caller = request.user
        is_owner = caller.id == target_user.id
        is_tenant_admin = caller.tenant_id == target_user.tenant_id and caller.role == "admin"
        is_super = caller.is_superuser or caller.role == "superadmin"

        if not (is_owner or is_tenant_admin or is_super):
            return Response(
                {"detail": "You do not have permission to view these credentials."},
                status=status.HTTP_403_FORBIDDEN,
            )

        extension = getattr(target_user, "extension", None)
        if not extension:
            return Response(
                {"detail": "No extension assigned to this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        decrypted_password = SecretService.decrypt(extension.encrypted_sip_password)
        effective_domain = target_user.effective_sip_domain

        return Response(
            {
                "extension_number": extension.extension_number,
                "sip_username": extension.sip_username,
                "sip_password": decrypted_password,
                "sip_domain": effective_domain,
                
                "transport_type": extension.transport_type,
            },
            status=status.HTTP_200_OK,
        )


class UserExtensionView(APIView):
    """
    POST   /api/v1/users/{id}/extension/ — Assign extension
    DELETE /api/v1/users/{id}/extension/ — Unassign extension
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        serializer = ExtensionAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ext_id = serializer.validated_data["extension_id"]
        extension = get_object_or_404(Extension, id=ext_id)

        # Ensure calling feature is enabled for tenant
        tenant = target_user.tenant or extension.tenant
        if not (tenant.features or {}).get("calling", False):
            return Response(
                {"detail": "Calling feature is disabled for this tenant. Cannot assign extension."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Ensure extension belongs to same tenant
        if target_user.tenant_id and extension.tenant_id != target_user.tenant_id:
            return Response(
                {"detail": "Extension does not belong to the user's tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        elif not target_user.tenant_id:
            target_user.tenant = extension.tenant
            target_user.save(update_fields=["tenant", "updated_at"])

        # Unassign previous extension from this user if any
        Extension.objects.filter(user=target_user).update(user=None)
        extension.user = target_user
        extension.save(update_fields=["user", "updated_at"])

        return Response({"status": "assigned", "extension_number": extension.extension_number}, status=status.HTTP_200_OK)

    def delete(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        Extension.objects.filter(user=target_user).update(user=None)
        return Response({"status": "unassigned"}, status=status.HTTP_200_OK)


class UserExtensionTransportView(APIView):
    """
    PATCH /api/v1/users/{id}/extension/transport/
    POST  /api/v1/users/{id}/extension/transport/
    Updates the transport type for the target user's assigned extension.
    RESTRICTED: Only superadmin can change transport type.
    """
    permission_classes = [IsSuperAdmin]

    def patch(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)

        extension = getattr(target_user, "extension", None)
        if not extension:
            return Response(
                {"detail": "No extension assigned to this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.extensions.serializers import ExtensionTransportUpdateSerializer
        serializer = ExtensionTransportUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_transport = serializer.validated_data["transport_type"]
        extension.transport_type = new_transport
        extension.save(update_fields=["transport_type", "updated_at"])

        return Response(
            {
                "id": str(extension.id),
                "extension_number": extension.extension_number,
                "sip_username": extension.sip_username,
                "transport_type": extension.transport_type,
                "updated_at": extension.updated_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, id, *args, **kwargs):
        return self.patch(request, id, *args, **kwargs)


class UserDIDView(APIView):
    """
    POST   /api/v1/users/{id}/dids/ — Grant DID access
    DELETE /api/v1/users/{id}/dids/{did_id}/ — Revoke DID access
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        serializer = DIDAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        did_id = serializer.validated_data["did_id"]
        did = get_object_or_404(DID, id=did_id)

        tenant = target_user.tenant or did.tenant
        calling_enabled = bool(tenant and (tenant.features or {}).get("calling", False))
        messaging_enabled = bool(tenant and (tenant.features or {}).get("messaging", False))

        if not calling_enabled and not messaging_enabled:
            return Response(
                {"detail": "Both calling and messaging features are disabled for this tenant. Cannot assign DIDs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target_user.tenant_id and did.tenant_id != target_user.tenant_id:
            return Response(
                {"detail": "DID does not belong to the user's tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        elif not target_user.tenant_id:
            target_user.tenant = did.tenant
            target_user.save(update_fields=["tenant", "updated_at"])

        assignment, created = UserDID.objects.get_or_create(user=target_user, did=did)
        return Response({"status": "assigned", "number": did.number, "created": created}, status=status.HTTP_200_OK)

    def delete(self, request, id, did_id=None, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        did = get_object_or_404(DID, id=did_id)
        UserDID.objects.filter(user=target_user, did=did).delete()
        return Response({"status": "revoked", "number": did.number}, status=status.HTTP_200_OK)


class UserFaxBoxView(APIView):
    """
    POST   /api/v1/users/{id}/fax-boxes/ — Assign FaxBox
    DELETE /api/v1/users/{id}/fax-boxes/{fax_uuid}/ — Remove FaxBox
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        if not (target_user.tenant and (target_user.tenant.features or {}).get("fax", False)):
            return Response(
                {"detail": "Fax feature is disabled for this tenant. Cannot assign fax boxes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = FaxBoxAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_box = serializer.validated_data
        existing_boxes = [b for b in target_user.fax_boxes if b.get("fax_uuid") != new_box["fax_uuid"]]
        existing_boxes.append(new_box)
        target_user.fax_boxes = existing_boxes
        target_user.save(update_fields=["fax_boxes", "updated_at"])

        return Response({"status": "assigned", "fax_boxes": target_user.fax_boxes}, status=status.HTTP_200_OK)

    def delete(self, request, id, fax_uuid=None, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        target_user.fax_boxes = [b for b in target_user.fax_boxes if b.get("fax_uuid") != str(fax_uuid)]
        target_user.save(update_fields=["fax_boxes", "updated_at"])
        return Response({"status": "removed", "fax_boxes": target_user.fax_boxes}, status=status.HTTP_200_OK)


class UserVoicemailBoxView(APIView):
    """
    POST   /api/v1/users/{id}/voicemail-boxes/ — Assign VoicemailBox
    DELETE /api/v1/users/{id}/voicemail-boxes/{box_id}/ — Remove VoicemailBox
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request, id, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        serializer = VoicemailBoxAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        box_id = serializer.validated_data["voicemail_box_id"]
        boxes = set(target_user.voicemail_boxes or [])
        boxes.add(box_id)
        target_user.voicemail_boxes = sorted(list(boxes))
        target_user.save(update_fields=["voicemail_boxes", "updated_at"])

        return Response({"status": "assigned", "voicemail_boxes": target_user.voicemail_boxes}, status=status.HTTP_200_OK)

    def delete(self, request, id, box_id=None, *args, **kwargs):
        target_user = get_object_or_404(User, id=id)
        target_user.voicemail_boxes = [b for b in target_user.voicemail_boxes if str(b) != str(box_id)]
        target_user.save(update_fields=["voicemail_boxes", "updated_at"])
        return Response({"status": "removed", "voicemail_boxes": target_user.voicemail_boxes}, status=status.HTTP_200_OK)
