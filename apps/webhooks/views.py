"""
apps/webhooks/views.py
───────────────────────
Inbound webhook ingestion endpoint for FreeSWITCH (and telephony carriers).

Architectural Invariants:
1. Secret Sanitization: Raw secrets (api_key, password, sip_password) are redacted
   BEFORE persisting to WebhookLog.
2. In-Memory api_key.created Handling:
   The plaintext api_key is encrypted immediately via SecretService and saved to Tenant,
   without ever passing through Celery task args or unencrypted storage.
3. Fast Acknowledgment: Returns HTTP 202 Accepted immediately.
4. Idempotency: WebhookLog stores provider_timestamp, event_type, object_id.
"""

import copy
import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, permissions, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.secret_service import SecretService
from apps.dids.models import DID
from apps.extensions.models import Extension
from apps.tenants.models import Tenant
from apps.webhooks.models import ProcessingStatus, WebhookLog

logger = logging.getLogger(__name__)

# Keys that must always be sanitized before persisting
SENSITIVE_KEYS = frozenset({"api_key", "password", "sip_password", "secret", "token"})


def sanitize_payload(obj):
    """Recursively redacts sensitive keys in JSON payloads."""
    if isinstance(obj, dict):
        sanitized = {}
        for key, value in obj.items():
            if key.lower() in SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, (dict, list)):
                sanitized[key] = sanitize_payload(value)
            else:
                sanitized[key] = value
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_payload(item) for item in obj]
    return obj


class FreeSwitchWebhookView(APIView):
    """
    Receives inbound FreeSWITCH webhooks.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        payload = request.data
        if not isinstance(payload, dict):
            return Response(
                {"error": "Invalid payload; expected a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = payload.get("event") or payload.get("event_type", "unknown")
        tenant_id = str(payload.get("tenant_id") or payload.get("tenant_uuid") or "").strip()
        tenant_code = str(payload.get("tenant_code", "")).strip()
        object_id = str(payload.get("object_id") or payload.get("call_uuid") or payload.get("fax_uuid") or payload.get("message_uuid") or "")

        # Parse provider timestamp
        raw_ts = payload.get("timestamp") or payload.get("provider_timestamp")
        provider_timestamp = None
        if raw_ts:
            try:
                provider_timestamp = parse_datetime(raw_ts)
            except Exception:
                provider_timestamp = None

        # Helper to resolve or auto-provision Tenant
        def resolve_or_create_tenant(auto_create=True):
            if not tenant_id:
                return None
            t = Tenant.objects.filter(freeswitch_tenant_uuid=tenant_id).first()
            if not t and tenant_code:
                t = Tenant.objects.filter(tenant_code=tenant_code).first()
            if not t:
                try:
                    t = Tenant.objects.filter(id=tenant_id).first()
                except Exception:
                    pass
            if not t and auto_create:
                code = tenant_code or "TENANT"
                name = payload.get("tenant_name") or f"{code} Tenant"
                t = Tenant.objects.create(
                    freeswitch_tenant_uuid=tenant_id,
                    tenant_code=code,
                    tenant_name=name,
                    encrypted_api_key="",
                    is_active=True,
                )
            return t

        # ------------------------------------------------------------------
        # 1. api_key.created / tenant.created: Synchronous in-memory encryption & provisioning
        # ------------------------------------------------------------------
        raw_api_key = payload.get("api_key")
        if event_type in ("api_key.created", "tenant.created") and tenant_id:
            try:
                code = tenant_code or "TENANT"
                name = payload.get("tenant_name") or f"{code} Tenant"
                domain = str(payload.get("sip_domain") or payload.get("domain") or "").strip()
                defaults_dict = {
                    "tenant_code": code,
                    "tenant_name": name,
                    "is_active": True,
                }
                if raw_api_key:
                    defaults_dict["encrypted_api_key"] = SecretService.encrypt(raw_api_key)
                elif not Tenant.objects.filter(freeswitch_tenant_uuid=tenant_id).exists():
                    defaults_dict["encrypted_api_key"] = ""

                if domain:
                    defaults_dict["sip_domain"] = domain

                tenant, created = Tenant.objects.update_or_create(
                    freeswitch_tenant_uuid=tenant_id,
                    defaults=defaults_dict,
                )
                logger.info(
                    "Provisioned FreeSWITCH tenant %s via %s (created=%s)",
                    tenant_id,
                    event_type,
                    created,
                )
            except Exception as exc:
                logger.error("Failed to process %s for tenant %s: %s", event_type, tenant_id, exc)

        # ------------------------------------------------------------------
        # 2. extension.created / extension.updated / extension.deleted
        # ------------------------------------------------------------------
        elif event_type in ("extension.created", "extension.updated") and object_id:
            tenant = resolve_or_create_tenant(auto_create=True)
            if tenant:
                ext = Extension.objects.filter(tenant=tenant, freeswitch_object_id=object_id).first()

                raw_num = payload.get("extension_number") or payload.get("phone")
                raw_sip_pw = payload.get("sip_password") or payload.get("password")
                raw_sip_user = payload.get("sip_username")
                raw_transport = payload.get("transport_type") or payload.get("transport")

                if ext:
                    # Partial update: preserve existing fields if not supplied in webhook payload
                    if raw_num:
                        ext.extension_number = str(raw_num)[:20]
                    if raw_sip_user:
                        ext.sip_username = str(raw_sip_user)
                    if raw_transport:
                        ext.transport_type = str(raw_transport)
                    if raw_sip_pw:
                        ext.encrypted_sip_password = SecretService.encrypt(raw_sip_pw)
                    ext.save()
                    logger.info("Extension %s updated for tenant %s", ext.extension_number, tenant.tenant_code)
                else:
                    # New extension
                    ext_num = str(raw_num)[:20] if raw_num else f"ext-{object_id[:8]}"
                    sip_user = raw_sip_user or f"{ext_num}-{tenant.tenant_code}"
                    transport = raw_transport or "TLS"
                    enc_pw = SecretService.encrypt(raw_sip_pw) if raw_sip_pw else ""

                    ext = Extension.objects.create(
                        tenant=tenant,
                        freeswitch_object_id=object_id,
                        extension_number=ext_num,
                        sip_username=sip_user,
                        transport_type=transport,
                        encrypted_sip_password=enc_pw,
                    )
                    logger.info("Extension %s created for tenant %s", ext.extension_number, tenant.tenant_code)

        elif event_type == "extension.deleted" and object_id:
            tenant = resolve_or_create_tenant(auto_create=False)
            if tenant:
                count, _ = Extension.objects.filter(tenant=tenant, freeswitch_object_id=object_id).delete()
                logger.info("Extension %s deleted for tenant %s (count=%s)", object_id, tenant.tenant_code, count)
            else:
                count, _ = Extension.objects.filter(freeswitch_object_id=object_id).delete()
                logger.info("Extension %s deleted globally (count=%s)", object_id, count)

        # ------------------------------------------------------------------
        # 3. did.created / did.updated / did.deleted
        # ------------------------------------------------------------------
        elif event_type in ("did.created", "did.updated") and object_id:
            tenant = resolve_or_create_tenant(auto_create=True)
            if tenant:
                did = DID.objects.filter(tenant=tenant, freeswitch_object_id=object_id).first()
                raw_num = payload.get("did_number") or payload.get("number") or payload.get("phone") or payload.get("did")
                raw_name = payload.get("did_name") or payload.get("name")

                if did:
                    if raw_num:
                        did.number = str(raw_num)[:20]
                    if raw_name is not None:
                        did.name = str(raw_name)[:255]
                    if "calling_enabled" in payload:
                        did.calling_enabled = bool(payload["calling_enabled"])
                    if "messaging_enabled" in payload:
                        did.messaging_enabled = bool(payload["messaging_enabled"])
                    did.save()
                    logger.info("DID %s (%s) updated for tenant %s", did.number, did.name, tenant.tenant_code)
                else:
                    did_num = str(raw_num)[:20] if raw_num else f"did-{object_id[:8]}"
                    did_name = str(raw_name)[:255] if raw_name else ""
                    did = DID.objects.create(
                        tenant=tenant,
                        freeswitch_object_id=object_id,
                        number=did_num,
                        name=did_name,
                        calling_enabled=payload.get("calling_enabled", True),
                        messaging_enabled=payload.get("messaging_enabled", True),
                    )
                    logger.info("DID %s (%s) created for tenant %s", did.number, did.name, tenant.tenant_code)

        elif event_type == "did.deleted" and object_id:
            tenant = resolve_or_create_tenant(auto_create=False)
            if tenant:
                count, _ = DID.objects.filter(tenant=tenant, freeswitch_object_id=object_id).delete()
                logger.info("DID %s deleted for tenant %s (count=%s)", object_id, tenant.tenant_code, count)
            else:
                count, _ = DID.objects.filter(freeswitch_object_id=object_id).delete()
                logger.info("DID %s deleted globally (count=%s)", object_id, count)

        # ------------------------------------------------------------------
        # Sanitize secrets before storing in WebhookLog
        # ------------------------------------------------------------------
        sanitized = sanitize_payload(payload)

        # Create temporary WebhookLog record (48h retention with indexed expires_at)
        log_entry = WebhookLog.objects.create(
            provider="freeswitch",
            event_type=event_type,
            object_id=object_id if object_id else None,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            provider_timestamp=provider_timestamp,
            payload=sanitized,
            processing_status=ProcessingStatus.PENDING,
        )

        logger.info(
            "FreeSWITCH webhook logged: id=%s event=%s tenant=%s object=%s",
            log_entry.id,
            event_type,
            tenant_id,
            object_id,
        )

        return Response(
            {
                "status": "accepted",
                "log_id": str(log_entry.id),
                "event": event_type,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class WebhookLogListView(generics.ListAPIView):
    """
    GET /api/v1/webhook-logs/
    Lists temporary 48-hour webhook records.
    """
    permission_classes = [permissions.IsAuthenticated]
    from apps.webhooks.serializers import WebhookLogSerializer
    serializer_class = WebhookLogSerializer

    def get_queryset(self):
        user = self.request.user
        qs = WebhookLog.objects.all()

        status_param = self.request.query_params.get("processing_status")
        if status_param:
            qs = qs.filter(processing_status=status_param)

        event_type = self.request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)

        if not user.is_superuser and user.role != "superadmin":
            if user.tenant and user.tenant.freeswitch_tenant_uuid:
                qs = qs.filter(tenant_id=str(user.tenant.freeswitch_tenant_uuid))
            else:
                qs = qs.none()

        return qs.order_by("-received_at")
