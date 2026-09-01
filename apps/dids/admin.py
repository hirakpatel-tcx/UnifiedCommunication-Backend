from django.contrib import admin
from apps.dids.models import DID, UserDID


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
        "calling_enabled",
        "messaging_enabled",
        "freeswitch_object_id",
        "created_at",
    )
    list_filter = ("tenant", "calling_enabled", "messaging_enabled")
    search_fields = ("number", "name", "freeswitch_object_id")
    inlines = [DIDUserInline]


@admin.register(UserDID)
class UserDIDAdmin(admin.ModelAdmin):
    list_display = ("user", "did", "created_at")
    list_filter = ("did__tenant",)
    search_fields = ("user__email", "did__number")
