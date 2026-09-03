"""
config/api_urls.py
Main API URL routing under /api/v1/
Wires up all REST endpoints defined in the API specification and Postman collection.
"""

from django.urls import include, path

from apps.audit.views import AuditLogListView
from apps.users.urls import auth_urlpatterns, user_urlpatterns
from apps.webhooks.views import WebhookLogListView

urlpatterns = [
    # 1. Authentication
    path("auth/", include(auth_urlpatterns)),

    # 2. Tenants Management
    path("tenants/", include("apps.tenants.urls")),

    # 3. Users Management & Resource Assignments
    path("users/", include(user_urlpatterns)),

    # Telephony Resources
    path("extensions/", include("apps.extensions.urls")),
    path("dids/", include("apps.dids.urls")),

    # Contacts & Directory
    path("contacts/", include("apps.contacts.urls")),

    # Voicemail Proxy
    path("voicemail/", include("apps.voicemail.urls")),

    # 6. Fax Proxy
    path("fax/", include("apps.common.fax_urls")),

    # 7. CDR & Analytics Proxy
    path("cdr/", include("apps.common.cdr_urls")),

    # 8. Call Recordings Proxy
    path("recordings/", include("apps.common.recording_urls")),

    # 8. Webhooks
    path("webhooks/", include("apps.webhooks.urls")),

    # 9. Audit & Monitoring Logs
    path("audit-logs/", AuditLogListView.as_view(), name="audit-logs-list"),
    path("webhook-logs/", WebhookLogListView.as_view(), name="webhook-logs-list"),
]
