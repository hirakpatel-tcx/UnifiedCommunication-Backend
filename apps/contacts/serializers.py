"""
apps/contacts/serializers.py
────────────────────────────
Serializers for Contact and ContactNumber models.
"""

from django.db import transaction
from rest_framework import serializers

from apps.contacts.models import Contact, ContactNumber, DirectoryType


class ContactNumberSerializer(serializers.ModelSerializer):
    """
    Serializer for phone numbers associated with a contact.
    """
    id = serializers.UUIDField(read_only=True)
    number = serializers.CharField(max_length=50, required=True)
    label = serializers.CharField(max_length=50, required=False, default="Mobile")
    is_primary = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = ContactNumber
        fields = [
            "id",
            "number",
            "label",
            "is_primary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_number(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Phone number cannot be empty.")
        return cleaned

    def validate_label(self, value):
        cleaned = (value or "").strip()
        return cleaned if cleaned else "Mobile"


class ContactSerializer(serializers.ModelSerializer):
    """
    Full contact serializer with nested contact numbers.
    Enforces that at least one phone number is provided.
    """
    numbers = ContactNumberSerializer(many=True, required=True)
    full_name = serializers.CharField(read_only=True)
    owner_email = serializers.EmailField(source="owner.email", read_only=True, allow_null=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, allow_null=True)

    class Meta:
        model = Contact
        fields = [
            "id",
            "tenant",
            "owner",
            "owner_email",
            "created_by",
            "created_by_email",
            "directory_type",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "notes",
            "is_favorite",
            "numbers",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant",
            "owner",
            "owner_email",
            "created_by",
            "created_by_email",
            "full_name",
            "created_at",
            "updated_at",
        ]

    def validate_first_name(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("First name is required.")
        return cleaned

    def validate(self, attrs):
        request = self.context.get("request")
        directory_type = attrs.get("directory_type", getattr(self.instance, "directory_type", DirectoryType.PERSONAL))

        # Check permissions for company directory creation/modification
        if directory_type == DirectoryType.COMPANY and request:
            user = request.user
            is_admin = getattr(user, "is_superuser", False) or getattr(user, "role", "") in ("admin", "superadmin")
            if not is_admin:
                raise serializers.ValidationError(
                    {"directory_type": "Only administrators can manage the Company Directory."}
                )

        # Enforce at least one number when creating
        if self.instance is None:
            numbers = attrs.get("numbers")
            if not numbers or len(numbers) == 0:
                raise serializers.ValidationError(
                    {"numbers": "At least one phone number is required to save a contact."}
                )
        else:
            # If updating and numbers key was supplied, it must not be empty
            if "numbers" in attrs:
                numbers = attrs.get("numbers")
                if not numbers or len(numbers) == 0:
                    raise serializers.ValidationError(
                        {"numbers": "A contact must have at least one phone number."}
                    )

        return attrs

    def create(self, validated_data):
        numbers_data = validated_data.pop("numbers", [])
        request = self.context.get("request")

        # Automatically assign tenant and owner
        tenant = self.context.get("tenant") or (request.user.tenant if request and request.user else None)
        directory_type = validated_data.get("directory_type", DirectoryType.PERSONAL)

        owner = None
        if directory_type == DirectoryType.PERSONAL and request and request.user:
            owner = request.user

        created_by = request.user if request and request.user else None

        with transaction.atomic():
            contact = Contact.objects.create(
                tenant=tenant,
                owner=owner,
                created_by=created_by,
                **validated_data,
            )

            # Ensure at least one number is marked primary if none was explicitly marked
            has_primary = any(n.get("is_primary") for n in numbers_data)
            for idx, num_data in enumerate(numbers_data):
                if not has_primary and idx == 0:
                    num_data["is_primary"] = True
                ContactNumber.objects.create(contact=contact, **num_data)

        return contact

    def update(self, instance, validated_data):
        numbers_data = validated_data.pop("numbers", None)

        with transaction.atomic():
            for attr, val in validated_data.items():
                setattr(instance, attr, val)
            instance.save()

            if numbers_data is not None:
                # Replace existing numbers atomically
                instance.numbers.all().delete()
                has_primary = any(n.get("is_primary") for n in numbers_data)
                for idx, num_data in enumerate(numbers_data):
                    if not has_primary and idx == 0:
                        num_data["is_primary"] = True
                    ContactNumber.objects.create(contact=instance, **num_data)

        return instance
