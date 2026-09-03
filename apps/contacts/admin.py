"""
apps/contacts/admin.py
──────────────────────
Django admin configuration for Contact and ContactNumber.
"""

from django.contrib import admin
from apps.contacts.models import Contact, ContactNumber


class ContactNumberInline(admin.TabularInline):
    model = ContactNumber
    extra = 1
    fields = ["number", "label", "is_primary"]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = [
        "full_name",
        "directory_type",
        "tenant",
        "owner",
        "email",
        "is_favorite",
        "created_at",
    ]
    list_filter = ["directory_type", "is_favorite", "tenant"]
    search_fields = ["first_name", "last_name", "email", "numbers__number"]
    inlines = [ContactNumberInline]
    readonly_fields = ["id", "created_at", "updated_at"]
