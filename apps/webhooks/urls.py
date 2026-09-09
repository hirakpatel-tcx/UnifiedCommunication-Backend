from django.urls import path
from apps.webhooks.views import FreeSwitchWebhookView, TelnyxWebhookView

urlpatterns = [
    path("freeswitch/", FreeSwitchWebhookView.as_view(), name="webhook-freeswitch"),
    path("telnyx/", TelnyxWebhookView.as_view(), name="webhook-telnyx"),
]
