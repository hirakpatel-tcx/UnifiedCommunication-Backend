"""
apps/common/cdr_views.py
────────────────────────
CDR and Call Analytics REST API endpoints proxying to FreeSWITCH / Cloud PBX Client API.

Enforces:
- Tenant feature flag check: 'calling' must be enabled.
- User scoping: regular users can be scoped to their assigned extension.
- Query parameter forwarding: forwards all analytics and filtering query params.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.common.tl_scoping import resolve_tl_extensions
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
    If the caller has TLGroupAccess grants (Team Lead reporting access), scopes
    queries to the extensions of callers assigned to the granted DID/Department
    combinations.
    """
    user = request.user
    if user.is_superuser:
        return params

    if getattr(user, "role", "") == "user":
        ext = getattr(user, "extension", None)
        if ext and ext.extension_number:
            params["extension"] = ext.extension_number
        return params

    extensions = resolve_tl_extensions(user)
    if extensions is not None:
        params["extension__in"] = ",".join(sorted(extensions))
    return params


def _resolve_requested_extensions(request, params: dict) -> Optional[list]:
    """
    Resolves the effective, final set of extensions a multi-extension-aware
    CDR endpoint should query — as a plain list, one upstream call per
    extension — after applying both the caller's own access scoping and any
    `extension`/`extension__in` they explicitly requested (e.g. picking one
    user, or a whole department's worth of extensions from the frontend's
    interconnected Department/DID/User filters).

    Returns None when the view should make a single unfiltered (tenant-wide)
    call — i.e. a superadmin/unrestricted admin who did not name specific
    extensions. Otherwise returns a de-duplicated, order-preserving list of
    one or more extension numbers/SIP usernames to fan out over.
    """
    # Scope against a params dict WITHOUT the caller's own multi-value
    # "extension" request, so _apply_user_extension_scoping's plain-"user"
    # branch (which unconditionally overwrites "extension" with the user's
    # own single extension) can't collide with — or be confused for — the
    # comma-separated value the caller asked for here.
    params_without_extension = {k: v for k, v in params.items() if k not in ("extension", "extension__in")}
    scoped = _apply_user_extension_scoping(request, params_without_extension)

    requested = None
    raw_multi = params.get("extension__in") or params.get("extension_ids")
    if raw_multi:
        requested = [e.strip() for e in raw_multi.split(",") if e.strip()]
    elif params.get("extension"):
        requested = [e.strip() for e in params["extension"].split(",") if e.strip()]

    allowed = None
    if "extension" in scoped:
        allowed = {scoped["extension"]}
    elif "extension__in" in scoped:
        allowed = set(scoped["extension__in"].split(","))

    if requested is None:
        if allowed is None:
            return None
        return sorted(allowed)

    if allowed is None:
        result = requested
    else:
        result = [e for e in requested if e in allowed]

    seen = set()
    deduped = []
    for e in result:
        if e not in seen:
            seen.add(e)
            deduped.append(e)
    return deduped


def _sum_numeric_tree(trees: list) -> dict:
    """
    Deep-merges a list of same-shaped dicts (as returned by tcxconnect's
    per-extension summary/daily-summary rows) by summing every numeric leaf.
    Non-numeric leaves (dates, labels) are taken from the first tree. Used to
    combine several single-extension upstream responses into one aggregate
    when the caller selected multiple extensions (e.g. a whole department).
    """
    if not trees:
        return {}
    result = {}
    first = trees[0]
    for key, value in first.items():
        if isinstance(value, dict):
            result[key] = _sum_numeric_tree([t.get(key, {}) for t in trees if isinstance(t.get(key), dict)])
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = sum(t.get(key, 0) or 0 for t in trees)
        else:
            result[key] = value
    return result


def _recompute_answer_rate(summary: dict) -> dict:
    """
    answer_rate is a derived percentage, not additive — summing per-extension
    answer_rate values would be meaningless, so after merging raw counts via
    _sum_numeric_tree, recompute it (and daily_summary's per-day rates) from
    the merged totals instead.
    """
    total = summary.get("total_calls", 0) or 0
    answered = (summary.get("outgoing", {}).get("answered", 0) or 0) + (
        summary.get("incoming", {}).get("answered", 0) or 0
    )
    summary["answer_rate"] = round((answered / total) * 100, 1) if total else 0.0
    return summary


def _merge_daily_summary(payloads: list) -> dict:
    """
    Merges multiple per-extension daily-summary payloads (each with a
    "results" list of per-date rows, zero-filled across the full requested
    range) by summing same-date rows together, then recomputing each day's
    (and the overall) answer_rate from the merged totals.
    """
    if not payloads:
        return {}
    if len(payloads) == 1:
        return payloads[0]

    by_date = {}
    date_order = []
    for payload in payloads:
        for row in payload.get("results", []):
            date = row.get("date")
            if date not in by_date:
                by_date[date] = []
                date_order.append(date)
            by_date[date].append(row)

    merged_results = []
    for date in date_order:
        merged_row = _sum_numeric_tree(by_date[date])
        total = merged_row.get("total_calls", 0) or 0
        answered = (merged_row.get("outgoing", {}).get("answered", 0) or 0) + (
            merged_row.get("incoming", {}).get("answered", 0) or 0
        )
        merged_row["answer_rate"] = round((answered / total) * 100, 1) if total else 0.0
        merged_results.append(merged_row)

    merged = _sum_numeric_tree(payloads)
    merged["results"] = merged_results
    total = merged.get("total_calls", 0) or 0
    merged["answer_rate"] = round(
        (sum(r.get("outgoing", {}).get("answered", 0) or 0 for r in merged_results)
         + sum(r.get("incoming", {}).get("answered", 0) or 0 for r in merged_results)) / total * 100, 1
    ) if total else 0.0
    return merged


def _merge_hourly_stats(payloads: list) -> dict:
    """
    Merges multiple per-extension hourly-stats payloads (each with an
    "hours" list of {hour, label, calls}) by summing same-hour buckets, and
    also returns a "by_extension" breakdown so a caller analyzing a whole
    department can still see each member's individual hourly pattern, not
    just the combined total.
    """
    if not payloads:
        return {}
    if len(payloads) == 1:
        single = dict(payloads[0])
        single["by_extension"] = {single.get("extension", ""): single.get("hours", [])}
        return single

    by_hour = {}
    for payload in payloads:
        for h in payload.get("hours", []):
            hour = h["hour"]
            if hour not in by_hour:
                by_hour[hour] = {"hour": hour, "label": h["label"], "calls": 0}
            by_hour[hour]["calls"] += h.get("calls", 0) or 0

    merged_hours = [by_hour[h] for h in sorted(by_hour.keys())]
    base = dict(payloads[0])
    base["hours"] = merged_hours
    base["total"] = sum(h["calls"] for h in merged_hours)
    base["extension"] = ""
    base["by_extension"] = {
        payload.get("extension", f"#{i}"): payload.get("hours", [])
        for i, payload in enumerate(payloads)
    }
    return base


def _fan_out_per_extension(tenant, endpoint_path: str, base_params: dict, extensions: list) -> list:
    """
    Issues one upstream call per extension concurrently (tcxconnect's CDR
    endpoints only accept a single `extension` value each), returning the
    list of successful response payloads in the same order as `extensions`.
    A per-extension failure is dropped rather than aborting the whole
    request — callers merge whatever succeeded.
    """
    def _fetch(ext):
        call_params = dict(base_params)
        call_params["extension"] = ext
        resp = FreeSwitchClientService.proxy_request(
            tenant=tenant, method="GET", endpoint_path=endpoint_path, params=call_params,
        )
        return resp if resp.status_code == status.HTTP_200_OK else None

    with ThreadPoolExecutor(max_workers=max(len(extensions), 1)) as executor:
        results = list(executor.map(_fetch, extensions))

    return [r.data for r in results if r is not None]


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


class CDRNotesView(APIView):
    """
    POST /api/v1/cdr/notes/
    Attaches a note to a call, keyed by the SIP Call-ID reported by the
    PJSIP client's call-state event. Proxied straight through to FreeSWITCH
    / Cloud PBX Client API, which owns the note (attaches it to the CDR row
    immediately if it already exists, or queues it for retry if the CDR for
    a just-ended call isn't ready yet).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        sip_call_id = request.data.get("sip_call_id")
        if not sip_call_id:
            return Response(
                {"detail": "sip_call_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="POST",
            endpoint_path="cdr/notes/",
            json_data={
                "sip_call_id": sip_call_id,
                "notes": request.data.get("notes", ""),
            },
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


class CDRDashboardView(APIView):
    """
    GET /api/v1/cdr/dashboard/
    Combines the existing summary, hourly-stats, daily-summary, and
    top-extensions proxies into one response, so the logs page dashboard
    doesn't have to fire four separate requests. Built for per-user AND
    per-department call analytics: `extension` accepts one value (a single
    user) or a comma-separated list (e.g. every extension in a department,
    as resolved by the frontend's interconnected Department/DID/User
    filters). tcxconnect's CDR endpoints only understand a single extension
    per call, so a multi-extension request fans out one upstream call per
    extension (concurrently) and merges the results:

    - summary, daily_summary: numeric counts are SUMMED across the selected
      extensions into one combined total (answer_rate is recomputed from
      the merged counts, not averaged) — this is what "department
      performance" means.
    - hourly_stats: the hour-by-hour call volume is summed into one
      combined curve, but a "by_extension" breakdown is also included so
      individual member patterns within a department remain visible.
    - top_extensions: inherently a per-extension breakdown already: when
      specific extensions were requested, it's filtered down to just those;
      left as a tenant-wide top-10 otherwise.

    A single extension (or no extension at all — the default, unrestricted
    tenant-wide view) takes the fast single-upstream-call path; only a
    multi-extension request pays the fan-out cost.

    Required params: start, end (ISO datetimes).
    Optional params: extension (single or comma-separated), utc_offset
    (default "+00:00").

    hourly-stats is inherently single-day (FreeSWITCH's Client API has no
    range variant), so it's computed for the LOCAL calendar day that `end`
    falls on (in utc_offset), consistent with how the rest of the dashboard
    treats `end` as "now"/the most recent point in the selected range.

    Each section carries its own upstream status; a failure in one section
    doesn't blank out the others — the affected section is returned as
    {"error": "..."} instead of aborting the whole response.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}

        start = params.get("start")
        end = params.get("end")
        if not start or not end:
            return Response(
                {"detail": "start and end are required."}, status=status.HTTP_400_BAD_REQUEST
            )
        utc_offset = params.get("utc_offset", "+00:00")

        extensions = _resolve_requested_extensions(request, params)
        base_params = {"start": start, "end": end}

        def _fetch_section(endpoint_path: str, extra_params: dict, merge_fn):
            call_params = {**base_params, **extra_params}
            if extensions is None:
                resp = FreeSwitchClientService.proxy_request(
                    tenant=tenant, method="GET", endpoint_path=endpoint_path, params=call_params,
                )
                if resp.status_code != status.HTTP_200_OK:
                    return {"error": resp.data.get("detail", "Upstream error") if isinstance(resp.data, dict) else "Upstream error"}
                return resp.data
            if len(extensions) == 0:
                return {"error": "No extensions in scope."}
            payloads = _fan_out_per_extension(tenant, endpoint_path, call_params, extensions)
            if not payloads:
                return {"error": "Upstream error for all requested extensions."}
            return merge_fn(payloads)

        hourly_params = {"date": end.split("T")[0], "utc_offset": utc_offset}

        with ThreadPoolExecutor(max_workers=4) as executor:
            summary_future = executor.submit(
                _fetch_section, "cdr/summary/", {}, lambda payloads: _recompute_answer_rate(_sum_numeric_tree(payloads))
            )
            daily_summary_future = executor.submit(
                _fetch_section, "cdr/daily-summary/", {}, _merge_daily_summary
            )
            hourly_stats_future = executor.submit(
                _fetch_section, "cdr/hourly-stats/", {"date": end.split("T")[0], "utc_offset": utc_offset}, _merge_hourly_stats
            )

            if extensions is None:
                top_extensions_future = executor.submit(
                    FreeSwitchClientService.proxy_request,
                    tenant=tenant, method="GET", endpoint_path="cdr/top-extensions/", params=base_params,
                )
            else:
                top_extensions_future = None

            summary = summary_future.result()
            daily_summary = daily_summary_future.result()
            hourly_stats = hourly_stats_future.result()

            if top_extensions_future is not None:
                resp = top_extensions_future.result()
                top_extensions = resp.data if resp.status_code == status.HTTP_200_OK else {
                    "error": resp.data.get("detail", "Upstream error") if isinstance(resp.data, dict) else "Upstream error"
                }
            else:
                # Already scoped to specific extensions — no need for a
                # separate tenant-wide top-10 call, since summary/daily_summary
                # already cover the requested set; per-extension totals for
                # comparison come from hourly_stats.by_extension instead.
                top_extensions = {"extensions": extensions}

        return Response(
            {
                "period": {"start": start, "end": end},
                "extensions": extensions,
                "summary": summary,
                "daily_summary": daily_summary,
                "top_extensions": top_extensions,
                "hourly_stats": hourly_stats,
            },
            status=status.HTTP_200_OK,
        )


class CDRCallsCountView(APIView):
    """
    GET /api/v1/cdr/calls-count/
    Live active call count for the tenant, proxied from FreeSWITCH via ESL.
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
            endpoint_path="calls/count/",
        )


