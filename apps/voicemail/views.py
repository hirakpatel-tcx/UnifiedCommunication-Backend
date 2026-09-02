"""
apps/voicemail/views.py
───────────────────────
Voicemail REST API endpoints proxying to FreeSWITCH / Cloud PBX Client API.

Enforces:
- Tenant feature flag check: 'voicemail' must be enabled.
- User mailbox scoping: regular users can only access their assigned User.voicemail_boxes.
- Secure API key injection: FreeSWITCH credentials are injected server-side.
- Streaming audio: voicemail WAV/MP3 files are streamed directly without buffering in RAM.
"""

from typing import Optional
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.freeswitch_client import FreeSwitchClientService


def _validate_voicemail_feature(tenant):
    if not getattr(tenant, "voicemail_enabled", False):
        return Response(
            {"detail": "Calling (and Voicemail) feature is disabled for this tenant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _get_scoped_mailbox_param(request, user) -> tuple[Optional[str], Optional[Response]]:
    """
    Returns (scoped_mailbox_id_str, error_response).
    If user is regular 'user', restricts to their assigned voicemail_boxes.
    """
    user_boxes = [str(b) for b in (user.voicemail_boxes or [])]
    requested_id = request.query_params.get("voicemail_id")

    if not user.is_superuser and getattr(user, "role", "") == "user":
        if not user_boxes:
            # User has no voicemail boxes assigned
            return None, Response(
                {"count": 0, "results": [], "detail": "No voicemail boxes are assigned to your account."},
                status=status.HTTP_200_OK,
            )

        if requested_id:
            # Check each requested id is within user's assigned boxes
            req_list = [x.strip() for x in requested_id.split(",") if x.strip()]
            for r in req_list:
                if r not in user_boxes:
                    return None, Response(
                        {"detail": f"You do not have permission to access voicemail box '{r}'."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            return requested_id, None
        else:
            # If no voicemail_id specified, automatically query user's assigned mailboxes
            return ",".join(user_boxes), None

    # Admin / superadmin: return requested_id as is (or None for summaries)
    return requested_id, None


class VoicemailMessagesView(APIView):
    """
    GET /api/v1/voicemail/messages/
    - If voicemail_id is provided (or user is scoped): returns paginated voicemail messages.
    - If voicemail_id is omitted (admin only): returns total and unread summaries for all mailboxes.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        scoped_boxes, err_resp = _get_scoped_mailbox_param(request, request.user)
        if err_resp:
            return err_resp

        params = dict(request.query_params)
        # flatten list values from QueryDict
        params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}

        if scoped_boxes:
            params["voicemail_id"] = scoped_boxes

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="voicemail-messages/",
            params=params,
        )


class VoicemailMessageDetailView(APIView):
    """
    GET    /api/v1/voicemail/messages/{message_uuid}/ — Message details
    DELETE /api/v1/voicemail/messages/{message_uuid}/ — Delete message
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, message_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path=f"voicemail-messages/{message_uuid}/",
        )

    def delete(self, request, message_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="DELETE",
            endpoint_path=f"voicemail-messages/{message_uuid}/",
        )


class VoicemailMessageMarkReadView(APIView):
    """
    PATCH /api/v1/voicemail/messages/{message_uuid}/mark-read/
    Body: {"read": true|false}
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, message_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="PATCH",
            endpoint_path=f"voicemail-messages/{message_uuid}/mark-read/",
            json_data=request.data,
        )


class VoicemailAudioStreamView(APIView):
    """
    GET /api/v1/voicemail/messages/{message_uuid}/audio/
    Streams audio/wav or audio/mpeg chunk-by-chunk from FreeSWITCH.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, message_uuid, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        return FreeSwitchClientService.proxy_stream(
            tenant=tenant,
            endpoint_path=f"voicemail-messages/{message_uuid}/audio/",
            default_content_type="audio/wav",
        )


class VoicemailUnreadCountsView(APIView):
    """
    GET /api/v1/voicemail/unread-counts/
    Returns unread counts by mailbox ID.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = FreeSwitchClientService.get_target_tenant(request)
        feat_err = _validate_voicemail_feature(tenant)
        if feat_err:
            return feat_err

        scoped_boxes, err_resp = _get_scoped_mailbox_param(request, request.user)
        if err_resp:
            return err_resp

        params = {}
        if scoped_boxes:
            params["voicemail_id"] = scoped_boxes

        return FreeSwitchClientService.proxy_request(
            tenant=tenant,
            method="GET",
            endpoint_path="voicemail-unread-counts/",
            params=params,
        )
