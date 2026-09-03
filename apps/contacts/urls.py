"""
apps/contacts/urls.py
─────────────────────
URL patterns for the Contacts module.
"""

from django.urls import path
from apps.contacts.views import ContactDetailView, ContactListCreateView

urlpatterns = [
    path("", ContactListCreateView.as_view(), name="contact-list-create"),
    path("<uuid:id>/", ContactDetailView.as_view(), name="contact-detail"),
]
