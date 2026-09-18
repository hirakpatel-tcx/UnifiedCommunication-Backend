"""
apps/downloads/views.py
─────────────────────────
DownloadLinkCreateView  — authenticated; issues a DesktopDownloadToken and
                           returns the public fetch URL to embed in email/UI.
DownloadFetchView       — unauthenticated (clicked from an email client);
                           validates the token and streams the installer.
"""

import logging

from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.downloads.models import DesktopDownloadToken, DesktopOS
from apps.downloads.services import UpstreamDownloadError, stream_release

logger = logging.getLogger(__name__)


class DownloadLinkCreateView(APIView):
    """
    POST /api/v1/downloads/link/
    Body: {"os": "win" | "mac" | "linux"}

    Issues a fetch URL that is valid for DOWNLOAD_TOKEN_TTL_HOURS starting
    from its first use. Intended to be embedded in the welcome/invite email
    in place of a direct update-server link.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        os_name = request.data.get("os")
        if os_name not in DesktopOS.values:
            return Response(
                {"detail": f"os must be one of {DesktopOS.values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = DesktopDownloadToken.objects.create(user=request.user, os=os_name)
        fetch_path = reverse("downloads-fetch", kwargs={"token": token.token})
        download_url = request.build_absolute_uri(fetch_path)

        return Response({"download_url": download_url}, status=status.HTTP_201_CREATED)


class DownloadFetchView(APIView):
    """
    GET /api/v1/downloads/fetch/<token>/

    No auth required — this is the link a user clicks from their email
    client. Validated by the opaque per-user token instead.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, token, *args, **kwargs):
        download_token = get_object_or_404(DesktopDownloadToken, token=token)

        if download_token.is_expired():
            return Response(
                {"detail": "This download link has expired. Please request a new one."},
                status=status.HTTP_410_GONE,
            )

        download_token.mark_used()

        try:
            return stream_release(download_token.os)
        except UpstreamDownloadError as exc:
            logger.error("Failed to stream %s installer: %s", download_token.os, exc)
            return Response(
                {"detail": "The installer is temporarily unavailable. Please try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
