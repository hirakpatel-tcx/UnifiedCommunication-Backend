"""Tests: fax inbox local filters (review_status / assignment)."""

import uuid
from unittest import mock

from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.common.models import FaxTag
from apps.common.services.freeswitch_client import FreeSwitchClientService
from apps.tenants.models import Tenant
from apps.users.models import User

URL = "/api/v1/fax/files/"


class FaxListLocalFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.t = Tenant.objects.create(
            freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="T1", tenant_name="One", features={"fax": True},
        )
        cls.admin = User.objects.create_user(email="a@x.com", password="x", tenant=cls.t, role="admin")
        cls.agent = User.objects.create_user(email="b@x.com", password="x", tenant=cls.t, role="admin")
        # f1: no tag (pending, unassigned); f2: in progress + assigned; f3: completed, unassigned
        cls.records = [{"fax_file_uuid": f"f{i}", "direction": "inbound"} for i in (1, 2, 3)]
        FaxTag.objects.create(tenant=cls.t, fax_file_uuid="f2", status="in_progress", assigned_to=cls.agent)
        FaxTag.objects.create(tenant=cls.t, fax_file_uuid="f3", status="completed")

    def setUp(self):
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        recs = [dict(r) for r in self.records]
        patcher = mock.patch.object(
            FreeSwitchClientService, "proxy_request",
            side_effect=lambda **kw: Response({"count": len(recs), "results": [dict(r) for r in recs]}),
        )
        self.proxy = patcher.start()
        self.addCleanup(patcher.stop)

    def ids(self, qs):
        r = self.c.get(URL + qs)
        self.assertEqual(r.status_code, 200, r.data)
        return sorted(x["fax_file_uuid"] for x in r.data["results"])

    def test_review_status_filters(self):
        self.assertEqual(self.ids("?direction=inbound&review_status=pending"), ["f1"])
        self.assertEqual(self.ids("?direction=inbound&review_status=in_progress"), ["f2"])
        self.assertEqual(self.ids("?direction=inbound&review_status=completed"), ["f3"])

    def test_assignment_filters_and_combination(self):
        self.assertEqual(self.ids("?assignment=assigned"), ["f2"])
        self.assertEqual(self.ids("?assignment=unassigned"), ["f1", "f3"])
        self.assertEqual(self.ids("?assignment=unassigned&review_status=completed"), ["f3"])

    def test_local_params_not_forwarded_to_pbx_and_count_is_filtered(self):
        r = self.c.get(URL + "?review_status=pending&page_size=1")
        self.assertEqual(r.data["count"], 1)
        sent = self.proxy.call_args.kwargs["params"]
        self.assertNotIn("review_status", sent)
        self.assertNotIn("assignment", sent)

    def test_invalid_values_rejected(self):
        self.assertEqual(self.c.get(URL + "?review_status=bogus").status_code, 400)
        self.assertEqual(self.c.get(URL + "?assignment=bogus").status_code, 400)
