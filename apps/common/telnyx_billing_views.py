"""
apps/common/telnyx_billing_views.py
────────────────────────────────────
Telnyx account billing/cost REST API endpoints, proxying to the Telnyx API.

Telnyx credentials are platform-wide (TELNYX_API_KEY), not per-tenant, and
this data spans the whole account rather than one tenant's traffic — so
these endpoints are superadmin-only.
"""

import calendar
from datetime import datetime, timezone

import httpx
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperAdmin
from apps.common.services.telnyx_client import TelnyxClientService

TELNYX_VOICE_PRODUCT = "sip-trunking"
TELNYX_MESSAGING_PRODUCT = "messaging"


def _current_month_range() -> tuple[str, str]:
    """ISO 8601 start/end covering the current calendar month to date."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


def _month_range(year: int, month: int) -> tuple[str, str]:
    """ISO 8601 start/end covering the given calendar month in full."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


def _telnyx_error_response(err: Exception) -> Response:
    if isinstance(err, httpx.HTTPStatusError):
        return Response(
            {"detail": f"Telnyx API error: {err.response.status_code}", "telnyx_response": err.response.text},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(err, (httpx.ConnectError, httpx.TimeoutException)):
        return Response(
            {"detail": "Unable to reach Telnyx API."},
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    return Response({"detail": str(err)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TelnyxMonthlyBillingView(APIView):
    """
    GET /api/v1/billing/telnyx/monthly/
    Grand total Telnyx cost for a calendar month across every billed
    product (voice/sip-trunking calls + SMS/messaging), plus the current
    account balance and each product's own subtotal with call/message
    counts.

    Note: Telnyx's usage_reports API only covers metered, pay-as-you-go
    usage. Recurring charges like monthly phone number rental, and account
    transactions like payments, are not exposed by this API and so are not
    included in this total.

    Query params:
      year, month — optional, both required together (e.g. ?year=2026&month=9).
                    Defaults to the current month to date when omitted.
    """
    permission_classes = [IsSuperAdmin]

    def get(self, request, *args, **kwargs):
        year = request.query_params.get("year")
        month = request.query_params.get("month")
        if year and month:
            try:
                start_date, end_date = _month_range(int(year), int(month))
            except ValueError:
                return Response({"detail": "year and month must be integers."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            start_date, end_date = _current_month_range()

        try:
            voice_usage = TelnyxClientService.get_usage_report(
                product=TELNYX_VOICE_PRODUCT,
                start_date=start_date,
                end_date=end_date,
                metrics=["cost", "completed", "billed_sec"],
                dimensions=["direction"],
            )
            messaging_usage = TelnyxClientService.get_usage_report(
                product=TELNYX_MESSAGING_PRODUCT,
                start_date=start_date,
                end_date=end_date,
                metrics=["cost"],
                dimensions=["date"],
            )
            balance = TelnyxClientService.get_balance()
        except Exception as err:
            return _telnyx_error_response(err)

        voice_rows = voice_usage.get("data", [])
        voice_cost = sum(float(row.get("cost", 0) or 0) for row in voice_rows)
        voice_calls = sum(int(row.get("completed", 0) or 0) for row in voice_rows)
        voice_billed_sec = sum(int(row.get("billed_sec", 0) or 0) for row in voice_rows)

        messaging_rows = messaging_usage.get("data", [])
        messaging_cost = sum(float(row.get("cost", 0) or 0) for row in messaging_rows)

        total_cost = voice_cost + messaging_cost

        return Response(
            {
                "period": {"start_date": start_date, "end_date": end_date},
                "total_cost": round(total_cost, 4),
                "currency": balance.get("currency", "USD"),
                "balance": balance,
                "voice": {
                    "cost": round(voice_cost, 4),
                    "calls_completed": voice_calls,
                    "billed_sec": voice_billed_sec,
                },
                "messaging": {
                    "cost": round(messaging_cost, 4),
                },
            },
            status=status.HTTP_200_OK,
        )


class TelnyxCallCostByDirectionView(APIView):
    """
    GET /api/v1/billing/telnyx/call-cost/
    Telnyx voice (sip-trunking) cost broken down by call direction
    (inbound / outbound).

    Query params:
      start_date, end_date — optional ISO 8601, max 31-day range (Telnyx limit).
                              Defaults to the current month to date when omitted.
    """
    permission_classes = [IsSuperAdmin]

    def get(self, request, *args, **kwargs):
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date:
            start_date, end_date = _current_month_range()

        try:
            usage = TelnyxClientService.get_usage_report(
                product=TELNYX_VOICE_PRODUCT,
                start_date=start_date,
                end_date=end_date,
                metrics=["cost"],
                dimensions=["direction"],
            )
        except Exception as err:
            return _telnyx_error_response(err)

        cost_by_direction = {"inbound": 0.0, "outbound": 0.0}
        for row in usage.get("data", []):
            direction = row.get("direction")
            if direction in cost_by_direction:
                cost_by_direction[direction] += float(row.get("cost", 0) or 0)

        return Response(
            {
                "period": {"start_date": start_date, "end_date": end_date},
                "cost_by_direction": {k: round(v, 4) for k, v in cost_by_direction.items()},
                "total_cost": round(sum(cost_by_direction.values()), 4),
            },
            status=status.HTTP_200_OK,
        )
