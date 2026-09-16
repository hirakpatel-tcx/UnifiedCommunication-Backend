"""
apps/common/recording_views.py
──────────────────────────────
Call Recordings REST API endpoints proxying to FreeSWITCH / Cloud PBX Client API.

Enforces:
- Tenant feature flag check: 'calling' must be enabled.
- Streaming audio: streams recorded audio directly without buffering in RAM.
"""

from concurrent.futures import ThreadPoolExecutor

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.freeswitch_client import FreeSwitchClientService

# Recordings fetched per extension before merging, when multiple extensions
# are requested (e.g. a whole department). Bounds how many rows we pull per
# upstream call — high enough that a merged, re-sorted, re-paginated result
# is correct for realistic page sizes without fetching a extension's entire
# history every request.
MULTI_EXTENSION_FETCH_LIMIT = 200


def _validate_calling_feature(tenant):
    if not (tenant.features or {}).get("calling", False):
        return Response(
            {"detail": "Calling feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


class CallRecordingListView(APIView):
    """
    GET /api/v1/recordings/
    Lists call recordings.
    Params: search, number, start, end, extension, page, page_size.

    extension: filters by the caller's extension — a single value ("101" or
    "101-TCX"), or a comma-separated list ("101,102,103") for multi-user /
    whole-department views. tcxconnect's own endpoint only understands one
    extension per call, so a multi-extension request fans out one call per
    extension (concurrently), then merges, re-sorts by start_stamp
    (descending, matching CallRecording's default ordering), and
    re-paginates the combined set here. A single extension (or none) takes
    the original single-upstream-call path.

    Populated on CallRecording via the linked CDR row at ingest time —
    recordings older than that link (or whose CDR link never resolved) have
    no extension_number and are excluded when this filter is used.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}

        raw_extension = params.get("extension", "")
        extensions = [e.strip() for e in raw_extension.split(",") if e.strip()]

        if len(extensions) <= 1:
            return FreeSwitchClientService.proxy_request(
                tenant=tenant,
                method="GET",
                endpoint_path="call-recordings/",
                params=params,
            )

        try:
            page = int(params.get("page", 1))
            page_size = min(int(params.get("page_size", 20)), 100)
        except ValueError:
            return Response({"detail": "page and page_size must be integers."}, status=status.HTTP_400_BAD_REQUEST)

        def _fetch(ext):
            call_params = {**params, "extension": ext, "page": 1, "page_size": MULTI_EXTENSION_FETCH_LIMIT}
            resp = FreeSwitchClientService.proxy_request(
                tenant=tenant, method="GET", endpoint_path="call-recordings/", params=call_params,
            )
            return resp.data.get("results", []) if resp.status_code == status.HTTP_200_OK else []

        with ThreadPoolExecutor(max_workers=len(extensions)) as executor:
            batches = list(executor.map(_fetch, extensions))

        merged = [row for batch in batches for row in batch]
        merged.sort(key=lambda r: r.get("start_stamp") or "", reverse=True)

        total = len(merged)
        offset = (page - 1) * page_size
        page_rows = merged[offset:offset + page_size]

        return Response(
            {
                "count": total,
                "next": page * page_size < total,
                "previous": page > 1,
                "results": page_rows,
            },
            status=status.HTTP_200_OK,
        )


class CallRecordingDetailView(APIView):
    """
    GET    /api/v1/recordings/{recording_uuid}/ — Metadata
    DELETE /api/v1/recordings/{recording_uuid}/ — Delete recording and audio file
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, recording_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path=f"call-recordings/{recording_uuid}/",
        )

    def delete(self, request, recording_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="DELETE",
            endpoint_path=f"call-recordings/{recording_uuid}/",
        )


class CallRecordingAudioStreamView(APIView):
    """
    GET /api/v1/recordings/{recording_uuid}/audio/
    Streams audio recording chunk-by-chunk from FreeSWITCH.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, recording_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_calling_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_stream(
            tenant=tenant,
            endpoint_path=f"call-recordings/{recording_uuid}/audio/",
            default_content_type="audio/wav",
        )
