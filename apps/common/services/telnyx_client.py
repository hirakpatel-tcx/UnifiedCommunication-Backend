"""
apps/common/services/telnyx_client.py
──────────────────────────────────────
Client service for the Telnyx Messaging (SMS/MMS) REST API.

Unlike FreeSwitchClientService, Telnyx credentials are a single platform-wide
API key (TELNYX_API_KEY), not per-tenant — tenants are distinguished by their
Telnyx Messaging Profile ID (Tenant.telnyx_messaging_profile_id).
"""

import logging
from typing import Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class TelnyxClientService:
    """HTTP client for Telnyx's Messaging API (`POST /v2/messages`, etc.)."""

    @classmethod
    def _headers(cls) -> dict:
        return {
            "Authorization": f"Bearer {settings.TELNYX_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @classmethod
    def _base_url(cls) -> str:
        return settings.TELNYX_API_BASE_URL.rstrip("/")

    @classmethod
    def get_balance(cls) -> dict:
        """
        Fetches the current account balance via `GET /v2/balance`.
        Returns the parsed JSON response body's "data" object.
        """
        url = f"{cls._base_url()}/v2/balance"
        timeout = getattr(settings, "TELNYX_API_TIMEOUT_SECONDS", 30.0)

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=cls._headers())
            resp.raise_for_status()
            return resp.json().get("data", {})

    @classmethod
    def get_usage_report(
        cls,
        product: str,
        start_date: str,
        end_date: str,
        metrics: Optional[list] = None,
        dimensions: Optional[list] = None,
    ) -> dict:
        """
        Fetches usage/cost data via `GET /v2/usage_reports`.

        `product` is required by Telnyx (e.g. "sip-trunking" for voice trunk
        usage). `start_date`/`end_date` must be ISO 8601 and span at most 31
        days per Telnyx's limit. `metrics` defaults to ["cost"]. `dimensions`
        is REQUIRED by Telnyx (a 400 "Dimensions invalid values" is returned
        if omitted, despite the public docs implying it's optional) — it
        defaults to ["date"], which still yields a single summable total
        when the caller only wants an aggregate.

        Telnyx expects repeated plain keys ("metrics=cost&metrics=connected"),
        NOT PHP/Rails-style bracket params ("metrics[]=cost") — sending
        bracket params here previously caused Telnyx to reject both metrics
        and dimensions as invalid, even when only one was actually malformed.

        Returns the parsed JSON response body (with "data" and "meta" keys).
        """
        url = f"{cls._base_url()}/v2/usage_reports"
        params = [
            ("product", product),
            ("start_date", start_date),
            ("end_date", end_date),
        ]
        params += [("metrics", m) for m in (metrics or ["cost"])]
        params += [("dimensions", d) for d in (dimensions or ["date"])]

        timeout = getattr(settings, "TELNYX_API_TIMEOUT_SECONDS", 30.0)

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=cls._headers(), params=params)
            resp.raise_for_status()
            return resp.json()

    @classmethod
    def send_message(
        cls,
        messaging_profile_id: str,
        from_number: str,
        to_numbers: list,
        text: str = "",
        media_urls: Optional[list] = None,
    ) -> dict:
        """
        Sends an SMS/MMS via `POST /v2/messages`. Telnyx handles group MMS
        fan-out natively when multiple `to` numbers are supplied.

        Returns the parsed JSON response body on success. Raises
        httpx.HTTPStatusError on a non-2xx response, and httpx.TimeoutException
        / httpx.ConnectError on network failures — callers translate these
        into API-facing errors.
        """
        url = f"{cls._base_url()}/v2/messages"
        payload = {
            "messaging_profile_id": messaging_profile_id,
            "from": from_number,
            "to": to_numbers if len(to_numbers) > 1 else to_numbers[0],
            "text": text,
        }
        if media_urls:
            payload["media_urls"] = media_urls

        timeout = getattr(settings, "TELNYX_API_TIMEOUT_SECONDS", 30.0)

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=cls._headers(), json=payload)
            resp.raise_for_status()
            return resp.json()
