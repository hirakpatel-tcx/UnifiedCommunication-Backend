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

import base64
import copy
import logging
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, permissions, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.common.services.secret_service import SecretService
from apps.dids.models import DID, UserDID
from apps.extensions.models import Extension
from apps.messaging.models import Message, MessageDirection, MessageStatus
from apps.messaging.services import resolve_conversation
from apps.outbox.models import OutboxEvent, OutboxTargetType
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
        elif event_type == "extension.updated" and object_id:
            tenant = resolve_or_create_tenant(auto_create=True)
            if tenant:
                fs_data = FreeSwitchClientService.get_resource(tenant, f"extensions/{object_id}/")
                if fs_data is None:
                    logger.error(
                        "extension.updated: failed to fetch extension %s from FreeSWITCH for tenant %s; skipping sync",
                        object_id, tenant.tenant_code,
                    )
                else:
                    ext = Extension.objects.filter(tenant=tenant, freeswitch_object_id=object_id).first()

                    raw_sip_pw = fs_data.get("password") or fs_data.get("sip_password")
                    raw_sip_user = fs_data.get("sip_username")
                    raw_transport = fs_data.get("transport_type") or fs_data.get("transport")

                    if ext:
                        # extension_number is not present in the FreeSWITCH extension
                        # payload (only sip_username) — left untouched on update.
                        if raw_sip_user:
                            ext.sip_username = str(raw_sip_user)
                        if raw_transport:
                            ext.transport_type = str(raw_transport)
                        if raw_sip_pw:
                            ext.encrypted_sip_password = SecretService.encrypt(raw_sip_pw)
                        ext.save()
                        logger.info("Extension %s synced from FreeSWITCH for tenant %s", ext.extension_number, tenant.tenant_code)

                        if ext.user_id:
                            OutboxEvent.objects.create(
                                tenant=tenant,
                                target_type=OutboxTargetType.USER,
                                target_id=str(ext.user_id),
                                event_type="extension.updated",
                                payload={"extension_id": str(ext.id), "requires_refresh": True, "reflect": True},
                            )
                    else:
                        ext_num = f"ext-{object_id[:8]}"
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
                        logger.info("Extension %s created from FreeSWITCH sync for tenant %s", ext.extension_number, tenant.tenant_code)

        elif event_type == "extension.created" and object_id:
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
                # freeswitch_object_id is only unique within a tenant, so without a
                # resolved tenant we cannot safely delete without risking a
                # cross-tenant match. Skip and log for investigation instead.
                logger.error(
                    "extension.deleted: could not resolve tenant (tenant_id=%s tenant_code=%s); "
                    "skipping deletion of extension %s to avoid cross-tenant match",
                    tenant_id, tenant_code, object_id,
                )

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
                    did.save()
                    logger.info("DID %s (%s) updated for tenant %s", did.number, did.name, tenant.tenant_code)

                    assigned_user_ids = UserDID.objects.filter(did=did).values_list("user_id", flat=True)
                    OutboxEvent.objects.bulk_create([
                        OutboxEvent(
                            tenant=tenant,
                            target_type=OutboxTargetType.USER,
                            target_id=str(user_id),
                            event_type="did.updated",
                            payload={"did_id": str(did.id), "requires_refresh": True, "reflect": True},
                        )
                        for user_id in assigned_user_ids
                    ])
                else:
                    did_num = str(raw_num)[:20] if raw_num else f"did-{object_id[:8]}"
                    did_name = str(raw_name)[:255] if raw_name else ""
                    did = DID.objects.create(
                        tenant=tenant,
                        freeswitch_object_id=object_id,
                        number=did_num,
                        name=did_name,
                    )
                    logger.info("DID %s (%s) created for tenant %s", did.number, did.name, tenant.tenant_code)

        elif event_type == "did.deleted" and object_id:
            tenant = resolve_or_create_tenant(auto_create=False)
            if tenant:
                count, _ = DID.objects.filter(tenant=tenant, freeswitch_object_id=object_id).delete()
                logger.info("DID %s deleted for tenant %s (count=%s)", object_id, tenant.tenant_code, count)
            else:
                # freeswitch_object_id is only unique within a tenant, so without a
                # resolved tenant we cannot safely delete without risking a
                # cross-tenant match. Skip and log for investigation instead.
                logger.error(
                    "did.deleted: could not resolve tenant (tenant_id=%s tenant_code=%s); "
                    "skipping deletion of DID %s to avoid cross-tenant match",
                    tenant_id, tenant_code, object_id,
                )

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


class TelnyxWebhookView(APIView):
    """
    Receives inbound Telnyx messaging webhooks (message.sent,
    message.finalized, message.received).

    Verifies the Ed25519 signature per Telnyx's webhook spec:
    https://developers.telnyx.com/docs/messaging/webhooks#authenticity
    header `telnyx-signature-ed25519` signs `{telnyx-timestamp}|{raw body}`.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def _verify_signature(self, request) -> bool:
        public_key_b64 = getattr(settings, "TELNYX_PUBLIC_KEY", "")
        if not public_key_b64:
            logger.error("TELNYX_PUBLIC_KEY is not configured; rejecting webhook.")
            return False

        signature_b64 = request.headers.get("telnyx-signature-ed25519")
        timestamp = request.headers.get("telnyx-timestamp")
        if not signature_b64 or not timestamp:
            return False

        try:
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
            signature = base64.b64decode(signature_b64)
            signed_payload = f"{timestamp}|".encode() + request.body
            public_key.verify(signature, signed_payload)
            return True
        except (InvalidSignature, ValueError, TypeError) as err:
            logger.error("Telnyx webhook signature verification failed: %s", err)
            return False

    def post(self, request, *args, **kwargs):
        if not self._verify_signature(request):
            return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)

        envelope = request.data if isinstance(request.data, dict) else {}
        payload = envelope.get("data", {}) if isinstance(envelope.get("data"), dict) else {}
        event_type = payload.get("event_type", "unknown")
        message_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
        telnyx_message_id = message_payload.get("id", "")

        if event_type in ("message.sent", "message.finalized"):
            self._handle_status_update(message_payload)
        elif event_type == "message.received":
            self._handle_inbound_message(message_payload)

        log_entry = WebhookLog.objects.create(
            provider="telnyx",
            event_type=event_type,
            object_id=telnyx_message_id or None,
            payload=envelope,
            processing_status=ProcessingStatus.PENDING,
        )

        logger.info("Telnyx webhook logged: id=%s event=%s message_id=%s", log_entry.id, event_type, telnyx_message_id)

        return Response({"status": "accepted", "event": event_type}, status=status.HTTP_202_ACCEPTED)

    def _handle_status_update(self, message_payload: dict):
        telnyx_message_id = message_payload.get("id")
        if not telnyx_message_id:
            return

        message = Message.objects.filter(telnyx_message_id=telnyx_message_id).first()
        if not message:
            return

        errors = message_payload.get("errors") or []
        telnyx_status = message_payload.get("to", [{}])[0].get("status") if message_payload.get("to") else None
        telnyx_status = telnyx_status or message_payload.get("status")

        if errors:
            message.status = MessageStatus.FAILED
            message.error_code = str(errors[0].get("code", ""))
            message.error_detail = errors[0].get("detail", "")
        elif telnyx_status == "delivered":
            message.status = MessageStatus.DELIVERED
            message.delivered_at = timezone.now()
        elif telnyx_status in ("sent", "sending_failed", "delivery_failed"):
            message.status = MessageStatus.SENT if telnyx_status == "sent" else MessageStatus.FAILED

        message.save(update_fields=["status", "error_code", "error_detail", "delivered_at", "updated_at"])

    def _handle_inbound_message(self, message_payload: dict):
        from_number = (message_payload.get("from") or {}).get("phone_number", "")
        to_entries = message_payload.get("to") or []
        to_numbers = [entry.get("phone_number") for entry in to_entries if entry.get("phone_number")]
        if not from_number or not to_numbers:
            logger.error("message.received missing from/to numbers: %s", message_payload)
            return

        did_number = to_numbers[0]
        did = DID.objects.filter(number=did_number).select_related("tenant").first()
        if not did:
            logger.error("message.received: no DID found for number %s", did_number)
            return

        tenant = did.tenant
        participant_numbers = [from_number] + [n for n in to_numbers if n != did_number]

        conversation = resolve_conversation(tenant, did, participant_numbers)

        media = message_payload.get("media") or []
        media_urls = [m.get("url") for m in media if m.get("url")]

        Message.objects.create(
            conversation=conversation,
            tenant=tenant,
            did=did,
            direction=MessageDirection.INBOUND,
            from_number=from_number,
            body=message_payload.get("text", ""),
            media_urls=media_urls,
            telnyx_message_id=message_payload.get("id"),
            status=MessageStatus.RECEIVED,
        )

        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["last_message_at", "updated_at"])


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
