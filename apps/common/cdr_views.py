"""
apps/common/cdr_views.py
────────────────────────
CDR and Call Analytics REST API endpoints proxying to FreeSWITCH / Cloud PBX Client API.

Enforces:
- Tenant feature flag check: 'calling' must be enabled.
- User scoping: regular users can be scoped to their assigned extension.
- Query parameter forwarding: forwards all analytics and filtering query params.
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.contacts.services import annotate_contact_flags


def _cdr_counterparty_field(record: dict) -> str:
    """
    The number to match against saved Contacts is whichever side of the call
    is NOT our own extension: the caller for inbound calls, the destination
    for outbound calls.
    """
    return "destination_number" if record.get("direction") == "outbound" else "caller_id_number"


def _validate_calling_feature(tenant):
    if not (tenant.features or {}).get("calling", False):
        return Response(
            {"detail": "Calling feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _annotate_cdr_response(tenant, response: Response) -> Response:
    """
    Adds contact_saved/contact_id (matched on the counterparty number) to
    each CDR record in the response body, whether it's a bare list, a
    paginated {"results": [...]} envelope, or a single-record detail dict.
    """
    if response.status_code != status.HTTP_200_OK:
        return response

    data = response.data
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        records = data["results"]
    elif isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # Single-record detail response.
        records = [data]
    else:
        records = None

    if records:
        annotate_contact_flags(tenant, records, _cdr_counterparty_field)

    return response


def _apply_user_extension_scoping(request, params: dict) -> dict:
    """
    If the caller is a standard 'user', scopes queries to their assigned extension.
    """
    user = request.user
    if not user.is_superuser and getattr(user, "role", "") == "user":
        ext = getattr(user, "extension", None)
        if ext and ext.extension_number:
            params["extension"] = ext.extension_number
    return params


class CDRListView(APIView):
    """
    GET /api/v1/cdr/
    Lists call records.
    Filters: direction, start, end, hangup_cause, missed_call, status, search, number, extension, export, page, page_size.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        params = _apply_user_extension_scoping(request, params)

        response = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/",
            params=params,
        )
        return _annotate_cdr_response(tenant, response)


class CDRDetailView(APIView):
    """
    GET /api/v1/cdr/{xml_cdr_uuid}/
    Retrieves detail of a single call record.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, xml_cdr_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        response = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path=f"cdr/{xml_cdr_uuid}/",
        )
        return _annotate_cdr_response(tenant, response)


class CDRSummaryView(APIView):
    """
    GET /api/v1/cdr/summary/
    Returns aggregate call statistics across the requested timeframe.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        params = _apply_user_extension_scoping(request, params)

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/summary/",
            params=params,
        )


class CDRHourlyStatsView(APIView):
    """
    GET /api/v1/cdr/hourly-stats/
    Required params: date, utc_offset, extension.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        params = _apply_user_extension_scoping(request, params)

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/hourly-stats/",
            params=params,
        )


class CDRDailySummaryView(APIView):
    """
    GET /api/v1/cdr/daily-summary/
    Required params: start, end.
    Optional params: extension, utc_offset.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        params = _apply_user_extension_scoping(request, params)

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/daily-summary/",
            params=params,
        )


class CDRTopExtensionsView(APIView):
    """
    GET /api/v1/cdr/top-extensions/
    Params: start, end.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/top-extensions/",
            params=dict(request.query_params),
        )


class CDRExtensionCallSummaryView(APIView):
    """
    GET /api/v1/cdr/extension-call-summary/
    Params: extension, start, end.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        params = _apply_user_extension_scoping(request, params)

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/extension-call-summary/",
            params=params,
        )


class CDRActiveExtensionsView(APIView):
    """
    GET /api/v1/cdr/active-extensions/
    Params: start, end.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="cdr/active-extensions/",
            params=dict(request.query_params),
        )
