from django.contrib import admin

from apps.common.models import FaxTag


@admin.register(FaxTag)
class FaxTagAdmin(admin.ModelAdmin):
    list_display = ("fax_file_uuid", "tenant", "assigned_to", "assigned_by", "status", "status_updated_by", "updated_at")
    list_filter = ("tenant", "status")
    search_fields = ("fax_file_uuid",)
