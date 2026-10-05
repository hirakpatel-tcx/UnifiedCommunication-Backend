from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.users.models import EmailLog, User
from apps.extensions.models import Extension
from apps.dids.models import UserDID, UserDIDDivisionAssignment


class ExtensionInline(admin.StackedInline):
    model = Extension
    extra = 0
    fields = ("extension_number", "sip_username", "transport_type", "freeswitch_object_id")
    readonly_fields = ("freeswitch_object_id",)


class UserDIDInline(admin.TabularInline):
    model = UserDID
    extra = 0
    autocomplete_fields = ("did",)


class UserDIDDivisionAssignmentInline(admin.TabularInline):
    model = UserDIDDivisionAssignment
    extra = 0
    verbose_name = "DID / Division Assignment"
    verbose_name_plural = "DID / Division Assignments"
    autocomplete_fields = ("did", "division")


class EverLoggedInFilter(admin.SimpleListFilter):
    title = "logged in"
    parameter_name = "logged_in"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No (never logged in)"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(last_login__isnull=False)
        if self.value() == "no":
            return queryset.filter(last_login__isnull=True)
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "tenant", "role", "is_team_lead", "sip_domain", "get_extension", "is_staff", "is_superuser", "is_active", "has_logged_in", "created_at")
    list_filter = ("role", "is_team_lead", "is_staff", "is_superuser", "is_active", "tenant", EverLoggedInFilter)
    inlines = [ExtensionInline, UserDIDInline, UserDIDDivisionAssignmentInline]
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

    @admin.display(description="Logged In?", boolean=True)
    def has_logged_in(self, obj):
        return obj.last_login is not None


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("to_email", "subject", "template", "status", "created_at")
    list_filter = ("status", "template")
    search_fields = ("to_email", "subject")
    readonly_fields = [f.name for f in EmailLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
