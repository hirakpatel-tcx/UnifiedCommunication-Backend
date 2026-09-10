"""
apps/common/fax_views.py
────────────────────────
Fax REST API endpoints proxying to FreeSWITCH / Cloud PBX Client API.

Enforces:
- Tenant feature flag check: 'fax' must be enabled.
- User scoping: regular users can only access their assigned User.fax_boxes.
- Multipart forwarding: handles PDF upload streams for outbound fax transmissions.
- Direct PDF streaming: streams downloaded fax documents without buffering in RAM.
"""

from typing import Optional
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models import FaxTag, FaxTagStatus
from apps.common.services.fax_tag_service import annotate_fax_tags
from apps.common.services.freeswitch_client import FreeSwitchClientService


def _validate_fax_feature(tenant):
    if not (tenant.features or {}).get("fax", False):
        return Response(
            {"detail": "Fax feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _get_scoped_fax_boxes_param(request, user) -> tuple[Optional[str], Optional[Response]]:
    user_boxes = [b.get("fax_uuid") for b in (user.fax_boxes or []) if b.get("fax_uuid")]
    requested_fax = request.query_params.get("fax")

    if not user.is_superuser and getattr(user, "role", "") == "user":
        if not user_boxes:
            return None, Response(
                {"count": 0, "results": [], "summary": {"total": 0}, "detail": "No fax boxes are assigned to your account."},
                status=status.HTTP_200_OK,
            )

        if requested_fax:
            req_list = [x.strip() for x in requested_fax.split(",") if x.strip()]
            for r in req_list:
                if r not in user_boxes:
                    return None, Response(
                        {"detail": f"You do not have permission to access fax box '{r}'."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            return requested_fax, None
        else:
            return ",".join(user_boxes), None

    return requested_fax, None


class FaxBoxListView(APIView):
    """
    GET /api/v1/fax/boxes/
    Lists all fax boxes for tenant (scoped for regular users).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        resp = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="fax/",
            params=dict(request.query_params),
        )

        # If regular user, filter returned list to only their assigned boxes
        if resp.status_code == 200 and not request.user.is_superuser and getattr(request.user, "role", "") == "user":
            allowed_uuids = {b.get("fax_uuid") for b in (request.user.fax_boxes or [])}
            if isinstance(resp.data, list):
                resp.data = [box for box in resp.data if box.get("fax_uuid") in allowed_uuids]
            elif isinstance(resp.data, dict) and "results" in resp.data:
                resp.data["results"] = [box for box in resp.data["results"] if box.get("fax_uuid") in allowed_uuids]
                resp.data["count"] = len(resp.data["results"])

        return resp


class FaxBoxDetailView(APIView):
    """
    GET /api/v1/fax/boxes/{fax_uuid}/
    Retrieves detail of a single fax box.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fax_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        # Check user permission
        if not request.user.is_superuser and getattr(request.user, "role", "") == "user":
            allowed_uuids = {b.get("fax_uuid") for b in (request.user.fax_boxes or [])}
            if str(fax_uuid) not in allowed_uuids:
                return Response(
                    {"detail": f"You do not have permission to access fax box '{fax_uuid}'."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path=f"fax/{fax_uuid}/",
        )


class FaxFileListView(APIView):
    """
    GET /api/v1/fax/files/
    Lists inbound and outbound fax transmissions.
    Query params: ?fax=...&status=received|sent|pending|failed&direction=inbound|outbound&search=...&page=...&page_size=...
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        scoped_fax, err_resp = _get_scoped_fax_boxes_param(request, request.user)
        if err_resp:
            return err_resp

        params = dict(request.query_params)
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}

        if scoped_fax:
            params["fax"] = scoped_fax

        resp = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="fax/files/",
            params=params,
        )

        if resp.status_code == 200:
            if isinstance(resp.data, dict) and isinstance(resp.data.get("results"), list):
                annotate_fax_tags(tenant, resp.data["results"])
            elif isinstance(resp.data, list):
                annotate_fax_tags(tenant, resp.data)

        return resp


class FaxFileDetailView(APIView):
    """
    GET    /api/v1/fax/files/{fax_file_uuid}/ — Transmission detail
    DELETE /api/v1/fax/files/{fax_file_uuid}/ — Delete fax transmission and disk file
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fax_file_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        resp = FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path=f"fax/files/{fax_file_uuid}/",
        )

        if resp.status_code == 200 and isinstance(resp.data, dict):
            annotate_fax_tags(tenant, [resp.data])

        return resp

    def delete(self, request, fax_file_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="DELETE",
            endpoint_path=f"fax/files/{fax_file_uuid}/",
        )


class FaxAssignmentView(APIView):
    """
    PATCH /api/v1/fax/files/{fax_file_uuid}/assignment/
    Body: {"assigned_to": "<user_id>"} and/or {"status": "pending"|"in_progress"|"completed"}

    Local triage workflow for medical fax review — FreeSWITCH has no concept
    of this, so it's tracked here keyed by fax_file_uuid. Lets an admin
    assign a pending fax to a specific user (shown as extension + name on
    the fax listing) and track review status.

    Permissions: admin/superadmin only for both fields — this is a triage
    control, not something the assignee edits on their own.
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, fax_file_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        user = request.user
        if not (user.is_superuser or getattr(user, "role", "") in ("superadmin", "admin")):
            return Response(
                {"detail": "Only admins can assign faxes or change review status."},
                status=status.HTTP_403_FORBIDDEN,
            )

        assigned_to_id = request.data.get("assigned_to")
        new_status = request.data.get("status")

        if assigned_to_id is None and new_status is None:
            return Response(
                {"detail": "Provide at least one of 'assigned_to' or 'status'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignee = None
        if assigned_to_id is not None:
            from apps.users.models import User
            assignee = User.objects.filter(id=assigned_to_id, tenant=tenant).first()
            if not assignee:
                return Response(
                    {"assigned_to": ["User not found in this tenant."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if new_status is not None and new_status not in FaxTagStatus.values:
            return Response(
                {"status": [f"Must be one of: {', '.join(FaxTagStatus.values)}."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fax_tag, _ = FaxTag.objects.get_or_create(
            tenant=tenant, fax_file_uuid=str(fax_file_uuid)
        )

        update_fields = ["updated_at"]
        if assigned_to_id is not None:
            fax_tag.assigned_to = assignee
            fax_tag.assigned_by = user
            update_fields += ["assigned_to", "assigned_by"]
        if new_status is not None:
            fax_tag.status = new_status
            fax_tag.status_updated_by = user
            update_fields += ["status", "status_updated_by"]

        fax_tag.save(update_fields=update_fields)

        extension = getattr(fax_tag.assigned_to, "extension", None) if fax_tag.assigned_to else None
        return Response(
            {
                "fax_file_uuid": fax_tag.fax_file_uuid,
                "assigned_to": {
                    "user_id": str(fax_tag.assigned_to_id),
                    "name": f"{fax_tag.assigned_to.first_name} {fax_tag.assigned_to.last_name}".strip(),
                    "extension_number": extension.extension_number if extension else None,
                } if fax_tag.assigned_to_id else None,
                "assigned_by": str(fax_tag.assigned_by_id) if fax_tag.assigned_by_id else None,
                "status": fax_tag.status,
                "status_updated_by": str(fax_tag.status_updated_by_id) if fax_tag.status_updated_by_id else None,
            },
            status=status.HTTP_200_OK,
        )


class FaxFileDownloadView(APIView):
    """
    GET /api/v1/fax/files/{fax_file_uuid}/download/
    Streams fax document as PDF directly from FreeSWITCH.
    Query param: ?attachment=true (forces browser download)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fax_file_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_stream(
            tenant=tenant,
            endpoint_path=f"fax/files/{fax_file_uuid}/download/",
            params=dict(request.query_params),
            default_content_type="application/pdf",
        )


class FaxSendView(APIView):
    """
    POST /api/v1/fax/send/
    Multipart form-data:
      - fax_uuid: UUID of sending fax box
      - destination_number: E.164 phone number
      - file: PDF document binary
    Proxies to FreeSWITCH POST /{tenant_uuid}/fax/quick-send/
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        fax_uuid = request.data.get("fax_uuid")
        destination = request.data.get("destination_number") or request.data.get("destination")
        uploaded_file = request.FILES.get("file") or request.FILES.get("document")

        if not fax_uuid:
            return Response({"fax_uuid": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if not destination:
            return Response({"destination_number": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if not uploaded_file:
            return Response({"file": ["A PDF file is required to transmit a fax."]}, status=status.HTTP_400_BAD_REQUEST)

        # Scoping check: regular users must own the sending fax_uuid
        if not request.user.is_superuser and getattr(request.user, "role", "") == "user":
            allowed_uuids = {b.get("fax_uuid") for b in (request.user.fax_boxes or [])}
            if str(fax_uuid) not in allowed_uuids:
                return Response(
                    {"detail": f"You do not have permission to send fax from box '{fax_uuid}'."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        form_data = {
            "fax_uuid": str(fax_uuid),
            "destination_number": str(destination),
        }
        files = {
            "file": (uploaded_file.name, uploaded_file.read(), uploaded_file.content_type or "application/pdf"),
        }

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="POST",
            endpoint_path="fax/quick-send/",
            form_data=form_data,
            files=files,
        )


class FaxFileCancelView(APIView):
    """
    POST /api/v1/fax/files/{fax_file_uuid}/cancel/
    Cancels an active or pending outbound fax.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, fax_file_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_fax_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="POST",
            endpoint_path=f"fax/files/{fax_file_uuid}/cancel/",
        )
