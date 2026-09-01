from django.contrib import admin
from apps.tenants.models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("tenant_name", "tenant_code", "sip_domain", "freeswitch_tenant_uuid", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("tenant_name", "tenant_code", "sip_domain", "freeswitch_tenant_uuid")
