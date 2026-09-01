from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.users.models import User
from apps.extensions.models import Extension
from apps.dids.models import UserDID


class ExtensionInline(admin.StackedInline):
    model = Extension
    extra = 0
    fields = ("extension_number", "sip_username", "transport_type", "freeswitch_object_id")
    readonly_fields = ("freeswitch_object_id",)


class UserDIDInline(admin.TabularInline):
    model = UserDID
    extra = 0
    autocomplete_fields = ("did",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "tenant", "role", "sip_domain", "get_extension", "is_staff", "is_superuser", "is_active", "created_at")
    list_filter = ("role", "is_staff", "is_superuser", "is_active", "tenant")
    inlines = [ExtensionInline, UserDIDInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Tenant & Role", {"fields": ("tenant", "role", "sip_domain")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Telephony Resources", {"fields": ("fax_boxes", "voicemail_boxes")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password", "tenant", "role", "sip_domain", "is_staff", "is_superuser", "is_active"),
            },
        ),
    )
    search_fields = ("email",)
    ordering = ("email",)

    @admin.display(description="Extension")
    def get_extension(self, obj):
        if hasattr(obj, "extension") and obj.extension:
            return obj.extension.extension_number
        return "-"
