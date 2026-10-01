"""
apps/users/management/commands/send_pending_invites.py
──────────────────────────────────────────────────────
Sends the welcome/invite email to every user who hasn't logged in yet
(is_first_login=True). Run this any time after `provision_rcm_data` —
it is a separate, deliberate step so new accounts can be reviewed before
anyone is emailed.

The original temp password set at creation time is never stored anywhere
(by design — see apps/users/tasks.py), so this command generates a FRESH
temp password for each pending user, sets it, and emails that one. Running
it twice emails everyone pending again with a new password each time — the
previous unused password is invalidated.

Usage:
    python manage.py send_pending_invites --tenant-code TCX
    python manage.py send_pending_invites --tenant-code TCX --dry-run
    python manage.py send_pending_invites --tenant-code TCX --email foo@bar.com --email baz@qux.com
"""

from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import Tenant
from apps.users.models import User
from apps.users.tasks import send_welcome_email
from apps.users.utils import generate_temp_password


class Command(BaseCommand):
    help = "Emails a fresh temp password to every user still pending first login."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-code",
            required=True,
            help="Only invite users belonging to this tenant.",
        )
        parser.add_argument(
            "--email",
            action="append",
            dest="emails",
            help="Limit to specific email(s). Repeatable. Omit to target everyone pending.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List who would be emailed without sending anything or changing passwords.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options):
        tenant_code = options["tenant_code"]
        dry_run = options["dry_run"]
        emails = options.get("emails")

        try:
            tenant = Tenant.objects.get(tenant_code=tenant_code)
        except Tenant.DoesNotExist:
            raise CommandError(f"No tenant with code '{tenant_code}'.")

        qs = User.objects.filter(tenant=tenant, is_first_login=True, is_active=True)
        if emails:
            qs = qs.filter(email__in=emails)
        pending = list(qs.order_by("email"))

        if not pending:
            self.stdout.write("No pending users to invite.")
            return

        self.stdout.write(f"{len(pending)} pending user(s):")
        for user in pending:
            self.stdout.write(f"  {user.email}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run — no emails sent."))
            return

        if not options["yes"]:
            confirm = input(f"\nSend invite emails to these {len(pending)} user(s)? [y/N] ").strip().lower()
            if confirm != "y":
                self.stdout.write("Aborted.")
                return

        sent = 0
        for user in pending:
            raw_password = generate_temp_password()
            user.set_password(raw_password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])

            send_welcome_email.delay(
                email=user.email,
                plaintext_password=raw_password,
                is_temp_password=True,
                first_name=user.first_name,
                user_id=user.id,
            )
            del raw_password
            sent += 1
            self.stdout.write(f"  queued invite: {user.email}")

        self.stdout.write(self.style.SUCCESS(f"\nQueued {sent} invite email(s)."))
