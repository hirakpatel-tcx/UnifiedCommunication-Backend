from django.contrib import admin
from apps.extensions.models import Extension


@admin.register(Extension)
class ExtensionAdmin(admin.ModelAdmin):
    list_display = (
        "extension_number",
        "tenant",
        "user",
        "sip_username",
        "transport_type",
        "freeswitch_object_id",
        "created_at",
    )
    list_filter = ("tenant", "transport_type")
    search_fields = ("extension_number", "sip_username", "freeswitch_object_id")
