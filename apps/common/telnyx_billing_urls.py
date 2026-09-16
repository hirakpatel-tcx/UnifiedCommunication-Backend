"""
apps/common/telnyx_billing_urls.py
────────────────────────────────────
URL routing for Telnyx billing/cost proxy endpoints.
"""

from django.urls import path
from apps.common.telnyx_billing_views import (
    TelnyxCallCostByDirectionView,
    TelnyxMonthlyBillingView,
)

urlpatterns = [
    path("monthly/", TelnyxMonthlyBillingView.as_view(), name="telnyx-monthly-billing"),
    path("call-cost/", TelnyxCallCostByDirectionView.as_view(), name="telnyx-call-cost-by-direction"),
]
