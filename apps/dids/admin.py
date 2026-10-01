from django.contrib import admin
from apps.dids.models import (
    DID,
    Department,
    Account,
    UserDID,
    Division,
    UserDIDDivisionAssignment,
    AccessGroup,
    AccessGroupEntry,
    TLGroupAccess,
)


class DIDUserInline(admin.TabularInline):
    model = UserDID
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "tenant", "created_at")
    list_filter = ("tenant",)
    search_fields = ("name", "code")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(DID)
class DIDAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "name",
        "department",
        "account",
        "tenant",
        "freeswitch_object_id",
        "created_at",
    )
    list_filter = ("tenant", "department", "account")
    search_fields = ("number", "name", "freeswitch_object_id")
    autocomplete_fields = ("department", "account")
    inlines = [DIDUserInline]


@admin.register(UserDID)
class UserDIDAdmin(admin.ModelAdmin):
    list_display = ("user", "did", "created_at")
    list_filter = ("did__tenant",)
    search_fields = ("user__email", "did__number")


@admin.register(Division)
class DivisionAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(UserDIDDivisionAssignment)
class UserDIDDivisionAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "did", "division", "department", "created_at")
    list_filter = ("division", "department", "did__tenant")
    search_fields = ("user__email", "did__number")
    autocomplete_fields = ("user", "did", "department")


class AccessGroupEntryInline(admin.TabularInline):
    model = AccessGroupEntry
    extra = 0
    autocomplete_fields = ("did", "department", "division")


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
