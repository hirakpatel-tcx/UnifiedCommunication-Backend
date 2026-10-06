from django import forms
from django.contrib import admin
from apps.tenants.models import Tenant

_FEATURE_CHOICES = [
    ("true",  "True (enabled)"),
    ("false", "False (disabled)"),
]


class TenantFeaturesForm(forms.ModelForm):
    feature_calling = forms.ChoiceField(choices=_FEATURE_CHOICES, required=True, label="Calling")
    feature_messaging = forms.ChoiceField(choices=_FEATURE_CHOICES, required=True, label="Messaging")
    feature_fax = forms.ChoiceField(choices=_FEATURE_CHOICES, required=True, label="Fax")

    class Meta:
        model = Tenant
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing = (self.instance.features or {}) if self.instance.pk else {}
        for key, field_name in (("calling", "feature_calling"), ("messaging", "feature_messaging"), ("fax", "feature_fax")):
            self.fields[field_name].initial = "true" if existing.get(key, False) else "false"

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.features = {
            key: self.cleaned_data.get(field_name) == "true"
            for key, field_name in (("calling", "feature_calling"), ("messaging", "feature_messaging"), ("fax", "feature_fax"))
        }
        if commit:
            instance.save()
        return instance


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    form = TenantFeaturesForm
    list_display = ("tenant_name", "tenant_code", "sip_domain", "freeswitch_tenant_uuid", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("tenant_name", "tenant_code", "sip_domain", "freeswitch_tenant_uuid")
    fieldsets = (
        (None, {"fields": ("tenant_name", "tenant_code", "freeswitch_tenant_uuid", "encrypted_api_key", "sip_domain", "is_active")}),
        ("Features", {
            "fields": ("feature_calling", "feature_messaging", "feature_fax"),
            "description": "Enable or disable communication features for this tenant.",
        }),
    )
