from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q

from apps.users.models import EmailLog, LoginHistory, User
from apps.users.tasks import send_welcome_email
from apps.users.utils import generate_temp_password
from apps.extensions.models import Extension
from apps.dids.models import UserDID, UserDIDDivisionAssignment

_INHERIT = "__inherit__"
_FEATURE_CHOICES = [
    (_INHERIT, "— Inherit from tenant —"),
    ("true",   "True (enabled)"),
    ("false",  "False (disabled)"),
]


class UserFeaturesFormMixin(forms.ModelForm):
    feature_calling = forms.ChoiceField(
        choices=_FEATURE_CHOICES, required=False, label="Calling",
        help_text="Inherit = use tenant setting.",
    )
    feature_messaging = forms.ChoiceField(
        choices=_FEATURE_CHOICES, required=False, label="Messaging",
        help_text="Inherit = use tenant setting.",
    )
    feature_fax = forms.ChoiceField(
        choices=_FEATURE_CHOICES, required=False, label="Fax",
        help_text="Inherit = use tenant setting.",
    )
    assign_extension = forms.ModelChoiceField(
        queryset=Extension.objects.none(), required=False, label="Extension",
        help_text="Unassigned extensions (searchable). Leave blank to keep the current assignment.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate dropdowns from the existing features dict
        existing = (self.instance.features or {}) if self.instance.pk else {}
        for key, field_name in (("calling", "feature_calling"), ("messaging", "feature_messaging"), ("fax", "feature_fax")):
            if key in existing:
                self.fields[field_name].initial = "true" if existing[key] else "false"
            else:
                self.fields[field_name].initial = _INHERIT

        # Candidates: any unassigned extension, plus the one already assigned
        # to this user (so it shows as selected instead of disappearing).
        # Scoped via autocomplete_fields (search, not a pre-rendered <select>),
        # so it doesn't need to filter by tenant before the field is usable.
        current_ext = getattr(self.instance, "extension", None) if self.instance.pk else None
        qs = Extension.objects.filter(Q(user__isnull=True) | Q(pk=current_ext.pk if current_ext else None))
        self.fields["assign_extension"].queryset = qs.order_by("tenant_id", "extension_number")
        if current_ext:
            self.fields["assign_extension"].initial = current_ext.pk

    def save(self, commit=True):
        # Call ModelForm.save directly (not super()) so this mixin never runs
        # through UserCreationForm.save, which expects password1/password2
        # fields that UserCreationFormForCustomUser removes.
        instance = forms.ModelForm.save(self, commit=False)
        features = {}
        for key, field_name in (("calling", "feature_calling"), ("messaging", "feature_messaging"), ("fax", "feature_fax")):
            val = self.cleaned_data.get(field_name, _INHERIT)
            if val == "true":
                features[key] = True
            elif val == "false":
                features[key] = False
            # _INHERIT → omit the key entirely
        instance.features = features
        if commit:
            instance.save()
            self.save_extension_assignment(instance)
        return instance

    def save_extension_assignment(self, instance):
        # The Django admin always calls form.save(commit=False) and persists
        # the instance itself via ModelAdmin.save_model — so this must also
        # be invoked explicitly from there (UserAdmin.save_model) for the
        # admin flow; the `if commit:` branch above only covers direct,
        # non-admin callers of form.save(commit=True).
        chosen = self.cleaned_data.get("assign_extension")
        current = getattr(instance, "extension", None)
        if chosen == current:
            return
        if current is not None:
            current.user = None
            current.save(update_fields=["user"])
        if chosen is not None:
            chosen.user = instance
            chosen.save(update_fields=["user"])


class UserCreationFormForCustomUser(UserFeaturesFormMixin, UserCreationForm):
    """
    Add-user form for the custom User model. No password fields are shown —
    a temporary password is always generated on save (see UserAdmin.save_model)
    and, when the admin clicks "Save and send invite email", mailed to the user.
    """
    password1 = None
    password2 = None

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)

    def clean_password2(self):
        return None

    def _post_clean(self):
        # Skip UserCreationForm._post_clean's password1/password2 validation
        # (those fields no longer exist on this form).
        forms.ModelForm._post_clean(self)


class UserFeaturesForm(UserFeaturesFormMixin):
    class Meta:
        model = User
        fields = "__all__"


class ExtensionInline(admin.StackedInline):
    """
    Read-only display of the extension currently assigned to this user.
    Assignment itself happens via the "Extension" dropdown in the main form
    (UserFeaturesFormMixin.assign_extension) so there is one place to change it.
    """
    model = Extension
    extra = 0
    max_num = 0
    fields = ("extension_number", "sip_username", "transport_type", "freeswitch_object_id")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


class LoginHistoryInline(admin.TabularInline):
    model = LoginHistory
    extra = 0
    fields = ("platform", "app_version", "created_at")
    readonly_fields = ("platform", "app_version", "created_at")
    ordering = ("-created_at",)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class LastLoginVersionFilter(admin.SimpleListFilter):
    title = "last login version"
    parameter_name = "last_login_version"

    def lookups(self, request, model_admin):
        versions = (
            LoginHistory.objects.order_by("app_version")
            .values_list("app_version", flat=True)
            .distinct()
        )
        return [(v, v) for v in versions]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        latest_ids = (
            LoginHistory.objects.filter(user_id__in=queryset.values_list("id", flat=True))
            .order_by("user_id", "-created_at")
            .distinct("user_id")
            .values_list("id", flat=True)
        )
        matching_user_ids = LoginHistory.objects.filter(
            id__in=latest_ids, app_version=self.value()
        ).values_list("user_id", flat=True)
        return queryset.filter(id__in=matching_user_ids)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserFeaturesForm
    add_form = UserCreationFormForCustomUser
    list_display = ("email", "first_name", "last_name", "tenant", "role", "is_team_lead", "sip_domain", "get_extension", "is_staff", "is_superuser", "is_active", "has_logged_in", "last_login_platform", "last_login_app_version", "created_at")
    list_filter = ("role", "is_team_lead", "is_staff", "is_superuser", "is_active", "tenant", EverLoggedInFilter, "login_history__platform", LastLoginVersionFilter)
    inlines = [ExtensionInline, UserDIDInline, UserDIDDivisionAssignmentInline, LoginHistoryInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal Info", {"fields": ("first_name", "last_name")}),
        ("Tenant & Role", {"fields": ("tenant", "role", "is_team_lead", "sip_domain")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Telephony Resources", {"fields": ("assign_extension", "fax_boxes", "voicemail_boxes")}),
        ("Feature Overrides", {
            "fields": ("feature_calling", "feature_messaging", "feature_fax"),
            "description": "Per-user feature overrides. 'Inherit' uses the tenant setting. Tenant is the ceiling — enabling here cannot exceed what the tenant allows.",
        }),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "tenant", "role", "sip_domain", "assign_extension", "feature_calling", "feature_messaging", "feature_fax", "is_staff", "is_superuser", "is_active"),
            },
        ),
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    autocomplete_fields = ("assign_extension",)
    change_form_template = "admin/users/user/change_form.html"
    add_form_template = "admin/users/user/change_form.html"

    def save_model(self, request, obj, form, change):
        if not change:
            # New user created from the admin: always provision a temp
            # password, same as the API invite flow (apps.users.utils.generate_temp_password).
            raw_password = generate_temp_password()
            obj.set_password(raw_password)
            obj.must_change_password = True
            obj._plaintext_password = raw_password
            obj._is_temp_password = True
        super().save_model(request, obj, form, change)
        form.save_extension_assignment(obj)
        if not change and "_saveandinvite" in request.POST:
            send_welcome_email.delay(
                email=obj.email,
                plaintext_password=obj._plaintext_password,
                is_temp_password=True,
                first_name=obj.first_name,
                user_id=obj.id,
            )
            messages.success(request, f"Invite email queued for {obj.email}.")
        elif change and "_resendinvite" in request.POST:
            # Same eligibility rule and behavior as UserReinviteView
            # (apps/users/views.py) — only for users still pending first
            # login; an onboarded user must use password reset instead.
            if not obj.is_first_login:
                messages.error(request, "User has already logged in; use password reset instead of reinvite.")
            elif not obj.is_active:
                messages.error(request, "Cannot reinvite an inactive user.")
            else:
                raw_password = generate_temp_password()
                obj.set_password(raw_password)
                obj.must_change_password = True
                obj.save(update_fields=["password", "must_change_password", "updated_at"])
                send_welcome_email.delay(
                    email=obj.email,
                    plaintext_password=raw_password,
                    is_temp_password=True,
                    first_name=obj.first_name,
                    user_id=obj.id,
                )
                messages.success(request, f"Invite email re-queued for {obj.email}.")

    def get_queryset(self, request):
        from django.db.models import Prefetch
        return super().get_queryset(request).prefetch_related(
            Prefetch("login_history", queryset=LoginHistory.objects.order_by("-created_at"))
        )

    @admin.display(description="Extension")
    def get_extension(self, obj):
        if hasattr(obj, "extension") and obj.extension:
            return obj.extension.extension_number
        return "-"

    @admin.display(description="Logged In?", boolean=True)
    def has_logged_in(self, obj):
        return obj.last_login is not None

    @admin.display(description="Last Login Platform")
    def last_login_platform(self, obj):
        latest = obj.login_history.first()
        return latest.get_platform_display() if latest else "-"

    @admin.display(description="Last Login Version")
    def last_login_app_version(self, obj):
        latest = obj.login_history.first()
        return latest.app_version if latest else "-"


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "app_version", "created_at")
    list_filter = ("platform",)
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in LoginHistory._meta.fields]
    autocomplete_fields = ("user",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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
