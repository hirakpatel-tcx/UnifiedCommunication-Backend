"""Tests: monitor-targets list, barge-request push, presence diff, CDR barge labels."""

import uuid

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.common.cdr_views import _annotate_extension_labels
from apps.extensions.models import Extension
from apps.extensions.presence import diff_presence, set_presence
from apps.tenants.models import Tenant
from apps.users.models import User

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
INMEM_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def mk_user(tenant, email, first, last, ext_no=None):
    u = User.objects.create_user(
        email=email, password="x", tenant=tenant, role="user", first_name=first, last_name=last,
    )
    if ext_no:
        Extension.objects.create(
            tenant=tenant, freeswitch_object_id=f"o-{ext_no}", extension_number=ext_no,
            sip_username=f"{ext_no}-T1", encrypted_sip_password="x", user=u,
        )
    return u


@override_settings(CACHES=LOCMEM, CHANNEL_LAYERS=INMEM_LAYER)
class BargeFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.t1 = Tenant.objects.create(freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="T1", tenant_name="One")
        cls.t2 = Tenant.objects.create(freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="T2", tenant_name="Two")
        cls.sup = mk_user(cls.t1, "sup@x.com", "Sam", "Super", "102")
        cls.agent = mk_user(cls.t1, "a@x.com", "Alice", "Agent", "601")
        cls.agent2 = mk_user(cls.t1, "b@x.com", "Bob", "Builder", "602")
        cls.alien = mk_user(cls.t2, "c@x.com", "Zed", "Other", "601")

    def setUp(self):
        cache.clear()
        self.c = APIClient()
        self.c.force_authenticate(self.sup)

    def test_list_excludes_self_and_other_tenants_and_shows_presence(self):
        set_presence(self.t1.pk, {"601": "online", "602": "online_busy"})
        r = self.c.get("/api/v1/extensions/monitor-targets/")
        self.assertEqual(r.status_code, 200)
        by_ext = {x["extension_number"]: x for x in r.data["results"]}
        self.assertEqual(set(by_ext), {"601", "602"})
        self.assertEqual(by_ext["601"]["status"], "online")
        self.assertEqual(by_ext["602"]["status"], "online_busy")
        self.assertEqual(by_ext["601"]["name"], "Alice Agent")

    def test_offline_when_no_presence(self):
        r = self.c.get("/api/v1/extensions/monitor-targets/")
        self.assertTrue(all(x["status"] == "offline" for x in r.data["results"]))

    def test_search_by_name_and_number(self):
        self.assertEqual([x["extension_number"] for x in
                          self.c.get("/api/v1/extensions/monitor-targets/?search=bui").data["results"]], ["602"])
        self.assertEqual([x["extension_number"] for x in
                          self.c.get("/api/v1/extensions/monitor-targets/?search=601").data["results"]], ["601"])

    def test_barge_request_reaches_target_group_only(self):
        layer = get_channel_layer()
        target_ch = async_to_sync(layer.new_channel)()
        other_ch = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f"user.{self.agent.pk}", target_ch)
        async_to_sync(layer.group_add)(f"user.{self.agent2.pk}", other_ch)

        r = self.c.post("/api/v1/extensions/barge-request/", {"user_id": str(self.agent.pk)}, format="json")
        self.assertEqual(r.status_code, 202)

        msg = async_to_sync(layer.receive)(target_ch)
        self.assertEqual(msg["event_type"], "barge.request")
        self.assertEqual(msg["payload"], {"supervisor_ext": "102", "supervisor_name": "Sam Super"})

    def test_barge_request_rejects_other_tenant_self_and_bad_input(self):
        post = lambda uid: self.c.post("/api/v1/extensions/barge-request/", {"user_id": uid}, format="json")
        self.assertEqual(post(str(self.alien.pk)).status_code, 404)
        self.assertEqual(post("not-a-uuid").status_code, 404)
        self.assertEqual(post(str(self.sup.pk)).status_code, 400)
        self.assertEqual(self.c.post("/api/v1/extensions/barge-request/", {}, format="json").status_code, 400)


class PresenceDiffTests(TestCase):
    def test_diff(self):
        self.assertEqual(diff_presence({"1": "online"}, {"1": "online_busy", "2": "online"}),
                         {"1": "online_busy", "2": "online"})
        self.assertEqual(diff_presence({"1": "online"}, {}), {"1": "offline"})
        self.assertEqual(diff_presence({"1": "online"}, {"1": "online"}), {})


class CdrBargeLabelTests(TestCase):
    def test_barge_codes_are_relabelled_others_untouched(self):
        labels = {"601": "Alice Agent - 601"}
        recs = [
            {"destination_number": "*35601", "extension_number": "102"},
            {"destination_number": "*34601"},
            {"destination_number": "*33601"},
            {"destination_number": "8005551234"},
        ]
        _annotate_extension_labels(recs, labels)
        self.assertEqual([r["destination_display"] for r in recs],
                         ["Barge 601", "Barge 601", "Barge 601", "8005551234"])
        self.assertEqual(recs[0]["barge_target_label"], "Alice Agent - 601")
        self.assertEqual(recs[0]["destination_number"], "*35601")
        self.assertNotIn("barge_target_label", recs[3])


@override_settings(CACHES=LOCMEM, CHANNEL_LAYERS=INMEM_LAYER)
class PresenceWebsocketTests(TransactionTestCase):
    def test_snapshot_on_connect_then_live_update_same_tenant_only(self):
        from channels.testing import WebsocketCommunicator
        from apps.extensions.presence import presence_group, push_to_group
        from apps.outbox.consumers import UserEventsConsumer

        cache.clear()
        t1 = Tenant.objects.create(freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="W1", tenant_name="W1")
        t2 = Tenant.objects.create(freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="W2", tenant_name="W2")
        me = mk_user(t1, "me@x.com", "Me", "Me", "102")
        agent = mk_user(t1, "ag@x.com", "Al", "Ag", "601")
        mk_user(t2, "o@x.com", "Ot", "Her", "701")
        set_presence(t1.pk, {"601": "online"})

        async def run():
            comm = WebsocketCommunicator(UserEventsConsumer.as_asgi(), "/ws/events/")
            comm.scope["user"] = me
            ok, _ = await comm.connect()
            self.assertTrue(ok)
            snap = await comm.receive_json_from()
            self.assertEqual(snap["event"], "presence.snapshot")
            by_ext = {p["extension_number"]: p["status"] for p in snap["data"]["presence"]}
            self.assertEqual(by_ext, {"102": "offline", "601": "online"})  # no tenant-2 ext

            await sync_to_async(push_to_group)(
                presence_group(t2.pk), "presence.update",
                {"changes": [{"extension_number": "701", "user_id": "x", "status": "online"}]},
            )
            await sync_to_async(push_to_group)(
                presence_group(t1.pk), "presence.update",
                {"changes": [{"extension_number": "601", "user_id": str(agent.pk), "status": "online_busy"}]},
            )
            msg = await comm.receive_json_from()
            self.assertEqual(msg["event"], "presence.update")
            self.assertEqual(msg["data"]["changes"][0]["status"], "online_busy")
            self.assertTrue(await comm.receive_nothing())
            await comm.disconnect()

        async_to_sync(run)()
