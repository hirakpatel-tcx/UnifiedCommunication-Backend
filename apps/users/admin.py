from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.users.models import User
from apps.extensions.models import Extension
from apps.dids.models import UserDID, UserDIDDepartmentAssignment


class ExtensionInline(admin.StackedInline):
    model = Extension
    extra = 0
    fields = ("extension_number", "sip_username", "transport_type", "freeswitch_object_id")
    readonly_fields = ("freeswitch_object_id",)


class UserDIDInline(admin.TabularInline):
    model = UserDID
    extra = 0
    autocomplete_fields = ("did",)


class UserDIDDepartmentAssignmentInline(admin.TabularInline):
    model = UserDIDDepartmentAssignment
    extra = 0
    verbose_name = "DID / Department Assignment"
    verbose_name_plural = "DID / Department Assignments"
    autocomplete_fields = ("did", "department")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "tenant", "role", "is_team_lead", "sip_domain", "get_extension", "is_staff", "is_superuser", "is_active", "created_at")
    list_filter = ("role", "is_team_lead", "is_staff", "is_superuser", "is_active", "tenant")
    inlines = [ExtensionInline, UserDIDInline, UserDIDDepartmentAssignmentInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal Info", {"fields": ("first_name", "last_name")}),
        ("Tenant & Role", {"fields": ("tenant", "role", "is_team_lead", "sip_domain")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Telephony Resources", {"fields": ("fax_boxes", "voicemail_boxes")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "password", "tenant", "role", "sip_domain", "is_staff", "is_superuser", "is_active"),
            },
        ),
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)

    @admin.display(description="Extension")
    def get_extension(self, obj):
        if hasattr(obj, "extension") and obj.extension:
            return obj.extension.extension_number
        return "-"
