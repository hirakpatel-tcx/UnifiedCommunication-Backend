"""
apps/common/cdr_urls.py
───────────────────────
URL routing for CDR and Call Analytics proxy endpoints.
"""

from django.urls import path
from apps.common.cdr_views import (
    CDRActiveExtensionsView,
    CDRDailySummaryView,
    CDRDetailView,
    CDRExtensionCallSummaryView,
    CDRHourlyStatsView,
    CDRListView,
    CDRNotesView,
    CDRSummaryView,
    CDRTopExtensionsView,
)

urlpatterns = [
    path("", CDRListView.as_view(), name="cdr-list"),
    path("notes/", CDRNotesView.as_view(), name="cdr-notes"),
    path("summary/", CDRSummaryView.as_view(), name="cdr-summary"),
    path("hourly-stats/", CDRHourlyStatsView.as_view(), name="cdr-hourly-stats"),
    path("daily-summary/", CDRDailySummaryView.as_view(), name="cdr-daily-summary"),
    path("top-extensions/", CDRTopExtensionsView.as_view(), name="cdr-top-extensions"),
    path("extension-call-summary/", CDRExtensionCallSummaryView.as_view(), name="cdr-extension-call-summary"),
    path("active-extensions/", CDRActiveExtensionsView.as_view(), name="cdr-active-extensions"),
    path("<str:xml_cdr_uuid>/", CDRDetailView.as_view(), name="cdr-detail"),
]
