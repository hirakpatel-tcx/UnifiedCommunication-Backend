from django.urls import path
from apps.extensions.views import (
    ExtensionDetailView,
    ExtensionListView,
    ExtensionTransportUpdateView,
)

urlpatterns = [
    path("", ExtensionListView.as_view(), name="extension-list"),
    path("<uuid:id>/", ExtensionDetailView.as_view(), name="extension-detail"),
    path("<uuid:id>/transport/", ExtensionTransportUpdateView.as_view(), name="extension-transport"),
]
