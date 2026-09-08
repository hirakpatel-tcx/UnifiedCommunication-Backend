"""
apps/tenants/management/commands/list_tenant_api_keys.py
──────────────────────────────────────────────────────────
Lists tenants in the terminal and lets you pick one to reveal its
decrypted FreeSWITCH PBX API key.

Usage:
    python manage.py list_tenant_api_keys
    python manage.py list_tenant_api_keys --tenant-code TCX
"""

from django.core.management.base import BaseCommand

from apps.common.services.secret_service import SecretService
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "List tenants and reveal a tenant's decrypted FreeSWITCH API key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-code",
            help="Tenant code to reveal the API key for directly, skipping the prompt.",
        )

    def handle(self, *args, **options):
        tenants = list(Tenant.objects.all().order_by("tenant_code"))
        if not tenants:
            self.stdout.write(self.style.WARNING("No tenants found."))
            return

        code = options.get("tenant_code")

        if not code:
            self.stdout.write("")
            self.stdout.write(f"{'#':<4}{'Code':<12}{'Name':<30}{'Active':<8}{'Has Key':<8}")
            self.stdout.write("-" * 62)
            for i, t in enumerate(tenants, start=1):
                self.stdout.write(
                    f"{i:<4}{t.tenant_code:<12}{t.tenant_name[:28]:<30}"
                    f"{str(t.is_active):<8}{str(bool(t.encrypted_api_key)):<8}"
                )
            self.stdout.write("")

            choice = input("Enter tenant # or code to reveal API key (blank to exit): ").strip()
            if not choice:
                return

            if choice.isdigit() and 1 <= int(choice) <= len(tenants):
                tenant = tenants[int(choice) - 1]
            else:
                tenant = next((t for t in tenants if t.tenant_code.lower() == choice.lower()), None)

            if not tenant:
                self.stdout.write(self.style.ERROR(f"No tenant matching '{choice}'."))
                return
        else:
            tenant = next((t for t in tenants if t.tenant_code.lower() == code.lower()), None)
            if not tenant:
                self.stdout.write(self.style.ERROR(f"No tenant with code '{code}'."))
                return

        if not tenant.encrypted_api_key:
            self.stdout.write(
                self.style.WARNING(f"Tenant {tenant.tenant_code} has no provisioned API key.")
            )
            return

        try:
            api_key = SecretService.decrypt(tenant.encrypted_api_key)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed to decrypt API key: {exc}"))
            return

        self.stdout.write("")
        self.stdout.write(f"Tenant:  {tenant.tenant_code} ({tenant.id})")
        self.stdout.write(f"API key: {api_key}")
