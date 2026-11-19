"""Tests: UserDetailSerializer.departments / divisions resolution and query cost."""

import uuid

from django.test import TestCase

from apps.dids.models import DID, Department, Division, UserDIDDivisionAssignment
from apps.tenants.models import Tenant
from apps.users.models import User
from apps.users.serializers import UserDetailSerializer, assignments_prefetch


class UserDepartmentsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            freeswitch_tenant_uuid=uuid.uuid4(), tenant_code="T1", tenant_name="One",
        )
        self.ar = Department.objects.create(tenant=self.tenant, name="AR")
        self.ev = Department.objects.create(tenant=self.tenant, name="EV")
        self.div = Division.objects.create(name="EVBV")
        self.did_ar = DID.objects.create(
            tenant=self.tenant, department=self.ar, freeswitch_object_id="d1", number="+15550001",
        )
        self.did_none = DID.objects.create(
            tenant=self.tenant, freeswitch_object_id="d2", number="+15550002",
        )

    def _user(self, email):
        return User.objects.create_user(email=email, password="x", tenant=self.tenant, role="user")

    def test_departments_fallback_override_and_dedupe(self):
        u = self._user("a@x.com")
        # falls back to DID.department (AR)
        UserDIDDivisionAssignment.objects.create(user=u, did=self.did_ar, division=self.div)
        # explicit override (EV) on a DID with no department
        UserDIDDivisionAssignment.objects.create(
            user=u, did=self.did_none, division=self.div, department=self.ev,
        )
        data = UserDetailSerializer(User.objects.get(pk=u.pk)).data
        self.assertEqual([d["name"] for d in data["departments"]], ["AR", "EV"])
        self.assertEqual([d["name"] for d in data["divisions"]], ["EVBV"])

    def test_no_assignments_gives_empty_lists(self):
        u = self._user("b@x.com")
        data = UserDetailSerializer(User.objects.get(pk=u.pk)).data
        self.assertEqual(data["departments"], [])
        self.assertEqual(data["divisions"], [])

    def test_prefetch_keeps_query_count_flat(self):
        for i in range(5):
            u = self._user(f"u{i}@x.com")
            UserDIDDivisionAssignment.objects.create(user=u, did=self.did_ar, division=self.div)

        qs = User.objects.filter(tenant=self.tenant).prefetch_related(assignments_prefetch())
        users = list(qs)
        with self.assertNumQueries(0):
            ser = UserDetailSerializer()
            for u in users:
                ser.get_departments(u)
