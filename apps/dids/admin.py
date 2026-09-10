from django.contrib import admin
from apps.dids.models import (
    DID,
    UserDID,
    Department,
    UserDIDDepartmentAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)


class DIDUserInline(admin.TabularInline):
    model = UserDID
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(DID)
class DIDAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "name",
        "tenant",
        "freeswitch_object_id",
        "created_at",
    )
    list_filter = ("tenant",)
    search_fields = ("number", "name", "freeswitch_object_id")
    inlines = [DIDUserInline]


@admin.register(UserDID)
class UserDIDAdmin(admin.ModelAdmin):
    list_display = ("user", "did", "created_at")
    list_filter = ("did__tenant",)
    search_fields = ("user__email", "did__number")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(UserDIDDepartmentAssignment)
class UserDIDDepartmentAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "did", "department", "created_at")
    list_filter = ("department", "did__tenant")
    search_fields = ("user__email", "did__number")
    autocomplete_fields = ("user", "did")


class AccessGroupEntryInline(admin.TabularInline):
    model = AccessGroupEntry
    extra = 0
    autocomplete_fields = ("did", "department")


@admin.register(AccessGroup)
class AccessGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name",)
    inlines = [AccessGroupEntryInline]


@admin.register(TLGroupAccess)
class TLGroupAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "created_at")
    list_filter = ("group",)
    search_fields = ("user__email", "group__name")
    autocomplete_fields = ("user", "group")
