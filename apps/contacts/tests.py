"""
apps/contacts/tests.py
──────────────────────
Comprehensive test suite for the Contacts and Directory module.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.contacts.models import Contact, ContactNumber, DirectoryType
from apps.tenants.models import Tenant
from apps.users.models import User, UserRole


class ContactAPITests(APITestCase):
    def setUp(self):
        # 1. Create primary tenant and users
        import uuid
        self.tenant_a = Tenant.objects.create(
            freeswitch_tenant_uuid=uuid.uuid4(),
            tenant_name="Acme Corp",
            tenant_code="ACM",
            sip_domain="acme.sip.local",
            features={"calling": True, "messaging": True},
            encrypted_api_key="",
        )
        self.admin_a = User.objects.create_user(
            email="admin@acme.com",
            password="StrongPassword123!",
            tenant=self.tenant_a,
            role=UserRole.ADMIN,
        )
        self.user_a1 = User.objects.create_user(
            email="alice@acme.com",
            password="StrongPassword123!",
            tenant=self.tenant_a,
            role=UserRole.USER,
        )
        self.user_a2 = User.objects.create_user(
            email="bob@acme.com",
            password="StrongPassword123!",
            tenant=self.tenant_a,
            role=UserRole.USER,
        )

        # 2. Create secondary tenant (for cross-tenant isolation testing)
        self.tenant_b = Tenant.objects.create(
            freeswitch_tenant_uuid=uuid.uuid4(),
            tenant_name="Beta LLC",
            tenant_code="BET",
            sip_domain="beta.sip.local",
            features={"calling": True, "messaging": True},
            encrypted_api_key="",
        )
        self.user_b = User.objects.create_user(
            email="charlie@beta.com",
            password="StrongPassword123!",
            tenant=self.tenant_b,
            role=UserRole.USER,
        )

        self.list_create_url = reverse("contact-list-create")

    # ------------------------------------------------------------------
    # 1. Validation Tests
    # ------------------------------------------------------------------

    def test_create_contact_requires_at_least_one_number(self):
        """Rejects contact creation if numbers array is empty or omitted."""
        self.client.force_authenticate(user=self.user_a1)

        payload = {
            "first_name": "John",
            "last_name": "Doe",
            "numbers": [],
        }
        res = self.client.post(self.list_create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("numbers", res.data)

    def test_create_contact_requires_first_name(self):
        """Rejects contact creation if first_name is missing or empty."""
        self.client.force_authenticate(user=self.user_a1)

        payload = {
            "first_name": "  ",
            "numbers": [{"number": "+12025550100", "label": "Mobile"}],
        }
        res = self.client.post(self.list_create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("first_name", res.data)

    # ------------------------------------------------------------------
    # 2. Personal Directory Tests
    # ------------------------------------------------------------------

    def test_create_personal_contact_with_multiple_numbers_and_custom_labels(self):
        """Standard user can create a personal contact with multiple numbers and custom labels."""
        self.client.force_authenticate(user=self.user_a1)

        payload = {
            "directory_type": "personal",
            "first_name": "Sarah",
            "last_name": "Connor",
            "email": "sarah@example.com",
            "notes": "VIP Client from Sector 4",
            "is_favorite": True,
            "numbers": [
                {"number": "+12025550111", "label": "Work", "is_primary": True},
                {"number": "+12025550122", "label": "Mobile"},
                {"number": "+12025550133", "label": "Direct Desk"},  # Custom label
                {"number": "+12025550144", "label": "Mobile"},       # Same label duplicate allowed
            ],
        }
        res = self.client.post(self.list_create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["first_name"], "Sarah")
        self.assertEqual(res.data["full_name"], "Sarah Connor")
        self.assertEqual(res.data["directory_type"], "personal")
        self.assertEqual(len(res.data["numbers"]), 4)

        # DB verification
        contact_id = res.data["id"]
        contact = Contact.objects.get(id=contact_id)
        self.assertEqual(contact.tenant, self.tenant_a)
        self.assertEqual(contact.owner, self.user_a1)
        self.assertEqual(contact.numbers.count(), 4)

    def test_personal_contact_isolation(self):
        """User A's personal contact is NOT visible or editable by User B."""
        # Alice creates a personal contact
        contact = Contact.objects.create(
            tenant=self.tenant_a,
            owner=self.user_a1,
            directory_type=DirectoryType.PERSONAL,
            first_name="AlicePrivate",
        )
        ContactNumber.objects.create(contact=contact, number="+12025559999", label="Personal")

        # Bob logs in and lists contacts
        self.client.force_authenticate(user=self.user_a2)
        res = self.client.get(self.list_create_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Handle paginated or non-paginated results
        results = res.data.get("results", res.data)
        ids = [item["id"] for item in results]
        self.assertNotIn(str(contact.id), ids)

        # Bob tries to access Alice's contact directly
        detail_url = reverse("contact-detail", kwargs={"id": contact.id})
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        # Bob tries to delete Alice's contact
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------------
    # 3. Company Directory Tests
    # ------------------------------------------------------------------

    def test_standard_user_cannot_create_company_contact(self):
        """Regular users are forbidden from creating Company Directory contacts."""
        self.client.force_authenticate(user=self.user_a1)

        payload = {
            "directory_type": "company",
            "first_name": "Acme",
            "last_name": "Helpdesk",
            "numbers": [{"number": "1000", "label": "Main"}],
        }
        res = self.client.post(self.list_create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("directory_type", res.data)

    def test_admin_can_create_company_contact(self):
        """Tenant admin can create a Company Directory contact."""
        self.client.force_authenticate(user=self.admin_a)

        payload = {
            "directory_type": "company",
            "first_name": "Support",
            "last_name": "Desk",
            "email": "support@acme.com",
            "numbers": [{"number": "1000", "label": "Queue", "is_primary": True}],
        }
        res = self.client.post(self.list_create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["directory_type"], "company")
        self.assertIsNone(res.data["owner"])

    def test_standard_user_can_view_but_cannot_modify_company_contact(self):
        """Standard users can read Company contacts, but cannot update or delete them."""
        # Admin creates company contact
        contact = Contact.objects.create(
            tenant=self.tenant_a,
            directory_type=DirectoryType.COMPANY,
            first_name="General",
            last_name="Reception",
        )
        ContactNumber.objects.create(contact=contact, number="0", label="Switchboard")

        # Alice logs in and can view the company contact
        self.client.force_authenticate(user=self.user_a1)
        detail_url = reverse("contact-detail", kwargs={"id": contact.id})

        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["first_name"], "General")

        # Alice tries to update the company contact -> 403 Forbidden
        patch_res = self.client.patch(detail_url, {"first_name": "Hacked"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_403_FORBIDDEN)

        # Alice tries to delete the company contact -> 403 Forbidden
        del_res = self.client.delete(detail_url)
        self.assertEqual(del_res.status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------------
    # 4. Search and Filtering Tests
    # ------------------------------------------------------------------

    def test_search_and_filters(self):
        """Supports searching across name, email, and phone numbers, and filtering by directory type."""
        self.client.force_authenticate(user=self.user_a1)

        # Create contacts
        c1 = Contact.objects.create(
            tenant=self.tenant_a,
            owner=self.user_a1,
            directory_type=DirectoryType.PERSONAL,
            first_name="Michael",
            last_name="Scott",
            email="michael@dundermifflin.com",
            is_favorite=True,
        )
        ContactNumber.objects.create(contact=c1, number="+15705550101", label="Work")

        c2 = Contact.objects.create(
            tenant=self.tenant_a,
            directory_type=DirectoryType.COMPANY,
            first_name="Dwight",
            last_name="Schrute",
            email="dwight@dundermifflin.com",
            is_favorite=False,
        )
        ContactNumber.objects.create(contact=c2, number="+15705550102", label="Desk")

        # Search by phone number
        res = self.client.get(f"{self.list_create_url}?search=5550101")
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["first_name"], "Michael")

        # Search by name
        res = self.client.get(f"{self.list_create_url}?search=Dwight")
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["first_name"], "Dwight")

        # Filter by directory_type=company
        res = self.client.get(f"{self.list_create_url}?directory_type=company")
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["first_name"], "Dwight")

        # Filter by is_favorite=true
        res = self.client.get(f"{self.list_create_url}?is_favorite=true")
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["first_name"], "Michael")

    # ------------------------------------------------------------------
    # 5. Cross-Tenant Isolation
    # ------------------------------------------------------------------

    def test_cross_tenant_isolation(self):
        """Contacts from Tenant A are completely invisible to users from Tenant B."""
        # Tenant A company contact
        contact_a = Contact.objects.create(
            tenant=self.tenant_a,
            directory_type=DirectoryType.COMPANY,
            first_name="Acme",
            last_name="Security",
        )
        ContactNumber.objects.create(contact=contact_a, number="911", label="Emergency")

        # Tenant B user queries contacts
        self.client.force_authenticate(user=self.user_b)
        res = self.client.get(self.list_create_url)
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 0)

        # Direct access to Tenant A contact
        detail_url = reverse("contact-detail", kwargs={"id": contact_a.id})
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------------
    # 6. Update and Delete Tests
    # ------------------------------------------------------------------

    def test_patch_and_put_contact_and_numbers(self):
        """Owner can update contact metadata and modify/replace phone numbers."""
        self.client.force_authenticate(user=self.user_a1)

        # Create personal contact
        contact = Contact.objects.create(
            tenant=self.tenant_a,
            owner=self.user_a1,
            directory_type=DirectoryType.PERSONAL,
            first_name="Pam",
            last_name="Beesly",
        )
        ContactNumber.objects.create(contact=contact, number="+15705550105", label="Reception")

        detail_url = reverse("contact-detail", kwargs={"id": contact.id})

        # PATCH: partial update fields
        patch_res = self.client.patch(
            detail_url,
            {"last_name": "Halpert", "notes": "Office Administrator", "is_favorite": True},
            format="json",
        )
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_res.data["full_name"], "Pam Halpert")
        self.assertEqual(patch_res.data["notes"], "Office Administrator")
        self.assertTrue(patch_res.data["is_favorite"])

        # PUT: replace numbers
        put_res = self.client.put(
            detail_url,
            {
                "first_name": "Pam",
                "last_name": "Halpert",
                "directory_type": "personal",
                "numbers": [
                    {"number": "+15705550199", "label": "Direct"},
                    {"number": "+15705550188", "label": "Home"},
                ],
            },
            format="json",
        )
        self.assertEqual(put_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(put_res.data["numbers"]), 2)
        numbers = [n["number"] for n in put_res.data["numbers"]]
        self.assertIn("+15705550199", numbers)
        self.assertIn("+15705550188", numbers)

    def test_delete_contact(self):
        """Deleting contact succeeds and cascade-deletes numbers."""
        self.client.force_authenticate(user=self.user_a1)

        contact = Contact.objects.create(
            tenant=self.tenant_a,
            owner=self.user_a1,
            directory_type=DirectoryType.PERSONAL,
            first_name="Ryan",
            last_name="Howard",
        )
        ContactNumber.objects.create(contact=contact, number="1005", label="Temp")

        detail_url = reverse("contact-detail", kwargs={"id": contact.id})
        del_res = self.client.delete(detail_url)
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)
        self.assertFalse(Contact.objects.filter(id=contact.id).exists())
        self.assertFalse(ContactNumber.objects.filter(contact_id=contact.id).exists())

    def test_pagination_and_export_all(self):
        """Validates page, page_size, and export_all parameters."""
        self.client.force_authenticate(user=self.user_a1)

        # Create 5 personal contacts
        for i in range(5):
            c = Contact.objects.create(
                tenant=self.tenant_a,
                owner=self.user_a1,
                directory_type=DirectoryType.PERSONAL,
                first_name=f"Contact{i}",
            )
            ContactNumber.objects.create(contact=c, number=f"555000{i}", label="Work")

        # 1. Test custom page_size=2
        res = self.client.get(f"{self.list_create_url}?page_size=2&page=1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)
        self.assertEqual(len(res.data["results"]), 2)
        self.assertEqual(res.data["count"], 5)

        # 2. Test export_all=true -> returns all records unpaginated directly
        res_export = self.client.get(f"{self.list_create_url}?export_all=true")
        self.assertEqual(res_export.status_code, status.HTTP_200_OK)
        self.assertIsInstance(res_export.data, list)
        self.assertEqual(len(res_export.data), 5)


