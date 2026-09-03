"""
apps/contacts/views.py
──────────────────────
API views for Contacts and Phonebook management.
"""

from django.db.models import Q
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.tenant_resolver import get_scoped_tenant
from apps.contacts.models import Contact, DirectoryType
from apps.contacts.permissions import IsContactOwnerOrCompanyAdmin
from apps.contacts.serializers import ContactSerializer


class ContactListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/contacts/ — List contacts with filtering, search, and directory scoping.
    POST /api/v1/contacts/ — Create a new contact with at least one phone number.
    """
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def _resolve_tenant(self):
        user = self.request.user
        if user.is_superuser or getattr(user, "role", "") == "superadmin":
            # If tenant_id / header is passed, resolve it
            raw_tenant = (
                self.request.query_params.get("tenant_id")
                or self.request.headers.get("X-Tenant-ID")
                or self.request.headers.get("x-tenant-id")
            )
            if raw_tenant:
                return get_scoped_tenant(self.request)
            return None
        return user.tenant

    def get_queryset(self):
        user = self.request.user
        tenant = self._resolve_tenant()

        qs = Contact.objects.all()
        if tenant:
            qs = qs.filter(tenant=tenant)
        elif not (user.is_superuser or getattr(user, "role", "") == "superadmin"):
            qs = qs.filter(tenant=user.tenant)

        # Scoping rules:
        # - Superadmin sees all in scope.
        # - Admin & User see: all Company contacts + their own Personal contacts.
        if not (user.is_superuser or getattr(user, "role", "") == "superadmin"):
            qs = qs.filter(
                Q(directory_type=DirectoryType.COMPANY)
                | Q(directory_type=DirectoryType.PERSONAL, owner=user)
            )

        # Directory type filter (?directory_type=company | personal)
        dir_type = self.request.query_params.get("directory_type")
        if dir_type:
            dir_type_clean = dir_type.strip().lower()
            if dir_type_clean in (DirectoryType.COMPANY, DirectoryType.PERSONAL):
                qs = qs.filter(directory_type=dir_type_clean)
            else:
                raise ValidationError(
                    {"directory_type": f"Invalid directory_type '{dir_type}'. Must be 'company' or 'personal'."}
                )

        # Favorite filter (?is_favorite=true | false)
        favorite = self.request.query_params.get("is_favorite")
        if favorite is not None:
            if favorite.lower() in ("true", "1"):
                qs = qs.filter(is_favorite=True)
            elif favorite.lower() in ("false", "0"):
                qs = qs.filter(is_favorite=False)

        # Search query (?search=...)
        search = self.request.query_params.get("search")
        if search:
            search = search.strip()
            qs = qs.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(notes__icontains=search)
                | Q(numbers__number__icontains=search)
                | Q(numbers__label__icontains=search)
            ).distinct()

        return (
            qs.select_related("owner", "tenant", "created_by")
            .prefetch_related("numbers")
            .order_by("first_name", "last_name", "-created_at")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        tenant = self._resolve_tenant()
        if tenant:
            context["tenant"] = tenant
        return context

    def perform_create(self, serializer):
        tenant = self._resolve_tenant()
        if not tenant:
            if self.request.user.tenant:
                tenant = self.request.user.tenant
            else:
                raise ValidationError({"tenant_id": "Tenant context could not be determined."})

        serializer.save()


class ContactDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/contacts/<id>/ — Retrieve contact details with all numbers.
    PUT    /api/v1/contacts/<id>/ — Replace contact details & numbers.
    PATCH  /api/v1/contacts/<id>/ — Partial update contact details & numbers.
    DELETE /api/v1/contacts/<id>/ — Delete contact and associated numbers.
    """
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated, IsContactOwnerOrCompanyAdmin]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        qs = Contact.objects.all()

        if not (user.is_superuser or getattr(user, "role", "") == "superadmin"):
            qs = qs.filter(tenant=user.tenant).filter(
                Q(directory_type=DirectoryType.COMPANY)
                | Q(directory_type=DirectoryType.PERSONAL, owner=user)
            )

        return (
            qs.select_related("owner", "tenant", "created_by")
            .prefetch_related("numbers")
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {"detail": "Contact deleted successfully."},
            status=status.HTTP_200_OK,
        )
