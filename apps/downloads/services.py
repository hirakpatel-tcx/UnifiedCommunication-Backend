"""
apps/downloads/services.py
────────────────────────────
Streams the desktop installer from update-server through Django, so the
update-server PUBLISH_TOKEN never leaves the backend (it's not embedded in
the download link, and it's never sent to the browser).
"""

import logging
import re
from typing import Generator

from django.conf import settings
from django.http import StreamingHttpResponse
import httpx

logger = logging.getLogger(__name__)


class UpstreamDownloadError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"update-server responded with {status_code}")


_LATEST_YML_NAMES = {
    "win": "latest.yml",
    "mac": "latest-mac.yml",
    "linux": "latest-linux.yml",
}
_YML_PATH_RE = re.compile(r"^\s*path:\s*(\S+)\s*$", re.MULTILINE)


def _resolve_current_installer_name(client: httpx.Client, base_url: str, os_name: str) -> str:
    """
    electron-updater's generic provider publishes filenames per-version
    (e.g. TCX-Connect-Setup-1.2.3.exe), discoverable via latest*.yml's
    `path:` field. Reading that avoids hardcoding a version in settings.
    """
    yml_url = f"{base_url}/releases/{os_name}/{_LATEST_YML_NAMES[os_name]}"
    resp = client.get(yml_url, headers={"Authorization": f"Bearer {settings.UPDATE_SERVER_PUBLISH_TOKEN}"})
    resp.raise_for_status()
    match = _YML_PATH_RE.search(resp.text)
    if not match:
        raise UpstreamDownloadError(502)
    return match.group(1)


def stream_release(os_name: str) -> StreamingHttpResponse:
    """
    Fetches the latest installer for `os_name` from update-server and streams
    it back chunk-by-chunk, without buffering the full file in RAM.
    """
    base_url = settings.UPDATE_SERVER_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.UPDATE_SERVER_PUBLISH_TOKEN}"}
    timeout = getattr(settings, "UPDATE_SERVER_TIMEOUT_SECONDS", 60.0)

    client = httpx.Client(timeout=timeout)

    try:
        filename = _resolve_current_installer_name(client, base_url, os_name)
    except httpx.HTTPStatusError as exc:
        client.close()
        logger.error("Failed to fetch %s manifest: %s", os_name, exc)
        raise UpstreamDownloadError(exc.response.status_code) from exc

    url = f"{base_url}/releases/{os_name}/{filename}"
    req = client.build_request("GET", url, headers=headers)
    upstream_resp = client.send(req, stream=True)

    if upstream_resp.status_code >= 400:
        upstream_resp.close()
        client.close()
        logger.error("update-server returned %s for %s", upstream_resp.status_code, url)
        raise UpstreamDownloadError(upstream_resp.status_code)

    content_type = upstream_resp.headers.get("Content-Type", "application/octet-stream")

    def file_iterator() -> Generator[bytes, None, None]:
        try:
            for chunk in upstream_resp.iter_bytes(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream_resp.close()
            client.close()

    response = StreamingHttpResponse(file_iterator(), content_type=content_type)
    for header_name in ("Content-Disposition", "Content-Length"):
        if header_name in upstream_resp.headers:
            response[header_name] = upstream_resp.headers[header_name]
    if "Content-Disposition" not in response:
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
