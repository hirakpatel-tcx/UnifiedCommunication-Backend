"""
apps/common/fax_urls.py
───────────────────────
URL routing for Fax proxy endpoints.
"""

from django.urls import path
from apps.common.fax_views import (
    FaxAssignmentView,
    FaxBoxDetailView,
    FaxBoxListView,
    FaxFileCancelView,
    FaxFileDetailView,
    FaxFileDownloadView,
    FaxFileListView,
    FaxSendView,
)

urlpatterns = [
    # Fax Boxes
    path("boxes/", FaxBoxListView.as_view(), name="fax-boxes-list"),
    path("boxes/<str:fax_uuid>/", FaxBoxDetailView.as_view(), name="fax-box-detail"),

    # Fax Transmissions / Files
    path("files/", FaxFileListView.as_view(), name="fax-files-list"),
    path("files/<str:fax_file_uuid>/", FaxFileDetailView.as_view(), name="fax-file-detail"),
    path("files/<str:fax_file_uuid>/download/", FaxFileDownloadView.as_view(), name="fax-file-download"),
    path("files/<str:fax_file_uuid>/cancel/", FaxFileCancelView.as_view(), name="fax-file-cancel"),
    path("files/<str:fax_file_uuid>/assignment/", FaxAssignmentView.as_view(), name="fax-file-assignment"),

    # Outbound Fax Send (quick-send)
    path("send/", FaxSendView.as_view(), name="fax-send"),
    path("quick-send/", FaxSendView.as_view(), name="fax-quick-send"),
]
