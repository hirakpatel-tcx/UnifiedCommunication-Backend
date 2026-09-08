"""
apps/common/services/freeswitch_client.py
─────────────────────────────────────────
Gateway service for server-to-server proxy communication with FreeSWITCH / Cloud PBX Client API.

Security invariants:
- Tenant-scoped FreeSWITCH API keys are decrypted strictly in-memory per request.
- Client applications authenticate with the backend via JWT; the backend securely injects
  the FreeSWITCH ApiKey header.
- Binary media (voicemail audio, fax PDFs) is streamed in chunks directly through
  StreamingHttpResponse without buffering full payloads in RAM.
"""

import logging
from typing import Generator, Optional
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
import httpx

from apps.common.services.secret_service import SecretService
from apps.common.tenant_resolver import get_scoped_tenant
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


class FreeSwitchClientService:
    """
    HTTP Proxy client for the FreeSWITCH / Cloud PBX Client API.
    """

    @classmethod
    def get_target_tenant(cls, request) -> Tenant:
        """
        Resolves the target tenant for the request using get_scoped_tenant.
        Superadmins must provide ?tenant_id=... or X-Tenant-ID header.
        Regular admins and users are automatically scoped to request.user.tenant.
        """
        return get_scoped_tenant(request)

    @classmethod
    def get_decrypted_api_key(cls, tenant: Tenant) -> str:
        """
        Retrieves and decrypts the tenant's API key in-memory.
        Raises ValidationError if key is missing or cannot be decrypted.
        """
        if not tenant.encrypted_api_key:
            raise ValidationError(
                {"detail": f"Tenant '{tenant.tenant_code}' has no provisioned PBX API key."}
            )
        try:
            key = SecretService.decrypt(tenant.encrypted_api_key)
            if not key:
                raise ValueError("Decrypted key is empty.")
            return key
        except Exception as err:
            logger.error("Failed to decrypt API key for tenant %s: %s", tenant.id, err)
            raise ValidationError(
                {"detail": f"Failed to decrypt PBX credentials for tenant '{tenant.tenant_code}'."}
            )

    @classmethod
    def build_url(cls, tenant: Tenant, endpoint_path: str) -> str:
        """
        Constructs the target FreeSWITCH URL:
        https://<pbx-domain>/api/v1/client/{tenant_uuid}/<resource>/
        """
        base = settings.FREESWITCH_CLIENT_API_BASE_URL.rstrip("/")
        path = endpoint_path.lstrip("/")
        return f"{base}/{tenant.freeswitch_tenant_uuid}/{path}"

    @classmethod
    def proxy_request(
        cls,
        tenant: Tenant,
        method: str,
        endpoint_path: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        form_data: Optional[dict] = None,
        files: Optional[dict] = None,
    ) -> Response:
        """
        Executes a JSON/Form request to FreeSWITCH and returns a DRF Response.
        """
        api_key = cls.get_decrypted_api_key(tenant)
        url = cls.build_url(tenant, endpoint_path)
        headers = {
            "Authorization": f"ApiKey {api_key}",
            "Accept": "application/json",
        }

        timeout = getattr(settings, "FREESWITCH_API_TIMEOUT_SECONDS", 30.0)

        # Filter out None values from params
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=clean_params or None,
                    json=json_data,
                    data=form_data,
                    files=files,
                )

                # Attempt to parse response body as JSON
                try:
                    data = resp.json()
                except Exception:
                    data = {"detail": resp.text} if resp.text else {}

                return Response(data=data, status=resp.status_code)

        except httpx.ConnectError as err:
            logger.error("FreeSWITCH connection error to %s: %s", url, err)
            return Response(
                {"detail": "Unable to connect to PBX telephony server. Connection refused."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except httpx.TimeoutException as err:
            logger.error("FreeSWITCH timeout on %s: %s", url, err)
            return Response(
                {"detail": "PBX telephony server timed out responding to request."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except Exception as err:
            logger.error("FreeSWITCH proxy exception on %s: %s", url, err, exc_info=True)
            return Response(
                {"detail": f"PBX proxy error: {str(err)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @classmethod
    def get_resource(
        cls,
        tenant: Tenant,
        endpoint_path: str,
        params: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Fetches a single resource from FreeSWITCH and returns the parsed JSON body,
        or None on any failure (connection error, timeout, non-2xx, unparsable body).
        For internal (non-request-scoped) callers such as webhook handlers.
        """
        api_key = cls.get_decrypted_api_key(tenant)
        url = cls.build_url(tenant, endpoint_path)
        headers = {
            "Authorization": f"ApiKey {api_key}",
            "Accept": "application/json",
        }
        timeout = getattr(settings, "FREESWITCH_API_TIMEOUT_SECONDS", 30.0)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers=headers, params=clean_params or None)
                if resp.status_code >= 400:
                    logger.error(
                        "FreeSWITCH get_resource non-2xx from %s: status=%s body=%s",
                        url, resp.status_code, resp.text,
                    )
                    return None
                return resp.json()
        except Exception as err:
            logger.error("FreeSWITCH get_resource error from %s: %s", url, err, exc_info=True)
            return None

    @classmethod
    def proxy_stream(
        cls,
        tenant: Tenant,
        endpoint_path: str,
        params: Optional[dict] = None,
        default_content_type: str = "application/octet-stream",
    ) -> StreamingHttpResponse:
        """
        Streams binary content (audio, PDF) chunk-by-chunk from FreeSWITCH directly to the client.
        Uses a generator so binary files are never buffered in server RAM.
        """
        api_key = cls.get_decrypted_api_key(tenant)
        url = cls.build_url(tenant, endpoint_path)
        headers = {
            "Authorization": f"ApiKey {api_key}",
        }
        timeout = getattr(settings, "FREESWITCH_API_TIMEOUT_SECONDS", 60.0)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        client = httpx.Client(timeout=timeout)

        try:
            req = client.build_request("GET", url, headers=headers, params=clean_params or None)
            upstream_resp = client.send(req, stream=True)

            if upstream_resp.status_code >= 400:
                # If error, consume small error body and return appropriate error
                try:
                    err_data = upstream_resp.json()
                except Exception:
                    err_data = {"detail": upstream_resp.text}
                upstream_resp.close()
                client.close()
                return Response(data=err_data, status=upstream_resp.status_code)

            content_type = upstream_resp.headers.get("Content-Type", default_content_type)

            def file_iterator() -> Generator[bytes, None, None]:
                try:
                    for chunk in upstream_resp.iter_bytes(chunk_size=8192):
                        if chunk:
                            yield chunk
                finally:
                    upstream_resp.close()
                    client.close()

            response = StreamingHttpResponse(file_iterator(), content_type=content_type)
            response.status_code = upstream_resp.status_code

            # Forward relevant content headers
            for header_name in ("Content-Disposition", "Content-Length", "Accept-Ranges"):
                if header_name in upstream_resp.headers:
                    response[header_name] = upstream_resp.headers[header_name]

            return response

        except Exception as err:
            client.close()
            logger.error("Streaming error from FreeSWITCH %s: %s", url, err, exc_info=True)
            return Response(
                {"detail": f"Failed to stream media from PBX server: {str(err)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
