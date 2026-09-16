from django.urls import path
from apps.extensions.views import (
    ExtensionActiveCallsView,
    ExtensionDetailView,
    ExtensionListView,
    ExtensionRegistrationsView,
    ExtensionTransportUpdateView,
)

urlpatterns = [
    path("", ExtensionListView.as_view(), name="extension-list"),
    path("registrations/", ExtensionRegistrationsView.as_view(), name="extension-registrations"),
    path("active-calls/", ExtensionActiveCallsView.as_view(), name="extension-active-calls"),
    path("<uuid:id>/", ExtensionDetailView.as_view(), name="extension-detail"),
    path("<uuid:id>/transport/", ExtensionTransportUpdateView.as_view(), name="extension-transport"),
]
