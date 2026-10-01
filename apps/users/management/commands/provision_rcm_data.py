"""
apps/users/management/commands/provision_rcm_data.py
──────────────────────────────────────────────────────
Idempotent one-shot provisioning for the RCM department/division/account/
DID/user setup built out in this session. Safe to re-run — every step uses
get_or_create, so running it twice does not create duplicates or touch
existing data.

Does NOT send any welcome/invite emails. New users are created with a
random temp password that is immediately discarded (never logged, never
persisted) and left with must_change_password=True / is_first_login=True.
Run `send_pending_invites` separately, whenever you're ready, to actually
email those users a fresh temp password.

Usage:
    python manage.py provision_rcm_data --tenant-code TCX
    python manage.py provision_rcm_data --tenant-code TCX --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.dids.models import Account, DID, Department, Division, UserDIDDivisionAssignment
from apps.extensions.models import Extension
from apps.tenants.models import Tenant
from apps.users.models import User
from apps.users.utils import generate_temp_password

import uuid

from apps.common.services.secret_service import SecretService


DEPARTMENTS = [
    "AR", "EV", "POSTING", "AUTHORIZATION", "DENIALS", "CREDENTIALS", "SUPPORT",
]

DIVISIONS = ["Medical", "Dental"]

ACCOUNTS = ["Rockwell", "Perry Ave", "Parcare", "General", "Excel", "Verrific"]

# (phone_number, department_name, account_name) — DIDs this command will
# attach a department/account to IF that DID already exists. It never
# creates DIDs (those are FreeSWITCH-managed and mirrored in via webhook).
DID_DEPARTMENT_ACCOUNT = [
    ("+19296682206", "CREDENTIALS", "General"),
    ("+19296682207", "AR", "General"),
    ("+19296682008", "EV", "Excel"),
    ("+19296682209", "EV", "Verrific"),
    ("+19296682210", "POSTING", "General"),
]

# (first, last, email, account_name_or_None, department_name_or_None, is_team_lead)
USERS = [
    ("Yogesh", "Chavan", "yogesh.c@technocruitx.com", None, None, True),
    ("Peshal", "Mistry", "peshal.m@technocruitx.com", None, None, False),
    ("Chittrang", "Thakkar", "chittrang.t@technocruitx.com", None, None, False),
    ("Fajal", "Khalifa", "fajal.k@technocruitx.com", "Excel", "EV", False),
    ("Hemant", "Dwivedi", "hemant.d@technocruitx.com", "Excel", "EV", False),
    ("Mahvish", "Rehman", "mahvish.r@technocruitx.com", "Excel", "EV", False),
    ("Mohammadali", "Pathan", "mohammadali.p@technocruitx.com", "Excel", "EV", False),
    ("Dhanraj", "Kuchara", "dhanraj.k@technocruitx.com", "Excel", "EV", False),
    ("Sachin", "Nimavat", "sachin.n@technocruitx.com", "Excel", "EV", False),
    ("Kamlesh", "Bhensala", "kamlesh.b@technocruitx.com", "Excel", "EV", False),
    ("Ashutosh", "Dwivedi", "ashutosh.d@technocruitx.com", "Excel", "EV", False),
    ("Namrata", "Sadhu", "namrata.s@technocruitx.com", "Excel", "EV", False),
    ("Jacquline", "Plaparambil", "jacquline.p@technocruitx.com", "General", "AR", False),
    ("Twinkle", "Garsia", "twinkle.g@technocruitx.com", "General", "AR", False),
    ("Naveed", "Ghori", "naveed.g@technocruitx.com", "General", "AR", False),
    ("Akshat", "Yadav", "akshat.y@technocruitx.com", "General", "AR", False),
    ("Manish", "Chandani", "manish.c@technocruitx.com", "General", "AR", False),
    ("Puja", "Das", "puja.d@technocruitx.com", "General", "AR", False),
    ("Aman", "Saiyed", "aman.sa@technocruitx.com", "Verrific", "EV", False),
    ("Gulfarojbanu", "Arodiya", "gulfarojbanu.a@technocruitx.com", "Verrific", "EV", False),
    ("Maharshi", "Shah", "maharshi.s@technocruitx.com", "Verrific", "EV", False),
    ("Arif", "Saiyad", "arif.s@technocruitx.com", "Verrific", "EV", False),
    ("Amit", "Dave", "amit.d@technocruitx.com", "Verrific", "EV", False),
    ("Dhanvi", "Thakor", "dhanvi.t@technocruitx.com", "Verrific", "EV", False),
    ("Tirthkumar", "Patel", "tirthkumar.p@technocruitx.com", "Verrific", "EV", False),
    ("Yashvadan", "Pavaskar", "yashvadan.p@technocruitx.com", "Verrific", "EV", False),
    ("Hiten", "Shah", "hiten.s@technocruitx.com", "Verrific", "EV", False),
    ("Sargam", "Kodekar", "sargam.k@technocruitx.com", "Verrific", "EV", False),
    ("Vinison", "Nongtdu", "vinison.n@technocruitx.com", "Verrific", "EV", False),
    ("Aamir", "Saiyad", "aamir.s@technocruitx.com", "Verrific", "EV", False),
    ("Vishnu", "Nair", "vishnu.n@technocruitx.com", "Verrific", "EV", False),
    ("Dhruv", "Mishra", "dhruv.m@technocruitx.com", "Verrific", "EV", False),
    ("Anash", "Mansuri", "anash.m@technocruitx.com", "Verrific", "EV", False),
    ("Azim", "Mirza", "azim.m@technocruitx.com", "Verrific", "EV", False),
    ("Grishma", "Sedani", "grishma.s@technocruitx.com", "Verrific", "EV", False),
    ("Vivek", "Narayankar", "vivek.n@technocruitx.com", "Verrific", "EV", False),
]

STARTING_EXTENSION = 204


class Command(BaseCommand):
    help = (
        "Idempotently provisions departments, divisions, accounts, DID "
        "department/account tagging, and the RCM user roster with "
        "extensions — without sending any invite emails."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-code",
            required=True,
            help="Tenant code to provision under (e.g. TCX).",
        )
        parser.add_argument(
            "--starting-extension",
            type=int,
            default=STARTING_EXTENSION,
            help=f"First extension number to use for newly created users (default {STARTING_EXTENSION}). "
                 "Existing extension numbers are skipped automatically.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without writing anything.",
        )

    def handle(self, *args, **options):
        tenant_code = options["tenant_code"]
        dry_run = options["dry_run"]
        next_ext = options["starting_extension"]

        try:
            tenant = Tenant.objects.get(tenant_code=tenant_code)
        except Tenant.DoesNotExist:
            raise CommandError(f"No tenant with code '{tenant_code}'.")

        with transaction.atomic():
            sid = transaction.savepoint()

            dept_map = self._provision_departments(tenant, dry_run)
            self._provision_divisions(dry_run)
            account_map = self._provision_accounts(dry_run)
            self._tag_dids(tenant, dept_map, account_map, dry_run)
            created_users = self._provision_users(tenant, dept_map, account_map, next_ext, dry_run)

            if dry_run:
                transaction.savepoint_rollback(sid)
                self.stdout.write(self.style.WARNING("\nDry run — no changes were committed."))
            else:
                transaction.savepoint_commit(sid)

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nDone. {len(created_users)} user(s) created."))
            if created_users:
                self.stdout.write(
                    "Run `python manage.py send_pending_invites --tenant-code "
                    f"{tenant_code}` when you're ready to email them their login details."
                )

    # ------------------------------------------------------------------

    def _provision_departments(self, tenant, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("Departments"))
        dept_map = {}
        for name in DEPARTMENTS:
            if dry_run:
                exists = Department.objects.filter(tenant=tenant, name=name).exists()
                self.stdout.write(f"  {'exists' if exists else 'would create'}: {name}")
                continue
            dept, created = Department.objects.get_or_create(tenant=tenant, name=name)
            dept_map[name] = dept
            self.stdout.write(f"  {'created' if created else 'exists'}: {name}")
        return dept_map

    def _provision_divisions(self, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("Divisions"))
        for name in DIVISIONS:
            if dry_run:
                exists = Division.objects.filter(name=name).exists()
                self.stdout.write(f"  {'exists' if exists else 'would create'}: {name}")
                continue
            _division, created = Division.objects.get_or_create(name=name)
            self.stdout.write(f"  {'created' if created else 'exists'}: {name}")

    def _provision_accounts(self, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("Accounts"))
        account_map = {}
        for name in ACCOUNTS:
            if dry_run:
                exists = Account.objects.filter(name=name).exists()
                self.stdout.write(f"  {'exists' if exists else 'would create'}: {name}")
                continue
            account, created = Account.objects.get_or_create(name=name)
            account_map[name] = account
            self.stdout.write(f"  {'created' if created else 'exists'}: {name}")
        return account_map

    def _tag_dids(self, tenant, dept_map, account_map, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("DID department/account tagging"))
        for number, dept_name, account_name in DID_DEPARTMENT_ACCOUNT:
            did = DID.objects.filter(tenant=tenant, number=number).first()
            if not did:
                self.stdout.write(self.style.WARNING(f"  skip {number}: DID not found (not yet synced from FreeSWITCH)"))
                continue
            if dry_run:
                self.stdout.write(f"  would tag {number}: department={dept_name}, account={account_name}")
                continue
            did.department = dept_map.get(dept_name) or Department.objects.get(tenant=tenant, name=dept_name)
            did.account = account_map.get(account_name) or Account.objects.get(name=account_name)
            did.save()
            self.stdout.write(f"  tagged {number}: department={dept_name}, account={account_name}")

    def _provision_users(self, tenant, dept_map, account_map, next_ext, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("Users"))
        division_dental = None if dry_run else Division.objects.get(name="Dental")
        created_users = []

        existing_ext_numbers = set(
            Extension.objects.filter(tenant=tenant).values_list("extension_number", flat=True)
        )

        for first, last, email, account_name, department_name, is_team_lead in USERS:
            if User.objects.filter(email=email).exists():
                self.stdout.write(f"  skip {email}: already exists")
                continue

            while str(next_ext) in existing_ext_numbers:
                next_ext += 1
            ext_number = str(next_ext)
            next_ext += 1
            existing_ext_numbers.add(ext_number)

            if dry_run:
                did_label = f"{account_name}/{department_name}" if account_name else "(none)"
                self.stdout.write(f"  would create {email} ext={ext_number} did_tag={did_label} team_lead={is_team_lead}")
                continue

            raw_password = generate_temp_password()
            user = User.objects.create_user(
                email=email,
                password=raw_password,
                tenant=tenant,
                first_name=first,
                last_name=last,
                role="user",
                is_team_lead=is_team_lead,
                is_first_login=True,
                must_change_password=True,
            )
            del raw_password  # never logged, never persisted

            Extension.objects.create(
                tenant=tenant,
                freeswitch_object_id=str(uuid.uuid4()),
                extension_number=ext_number,
                sip_username=f"{ext_number}-{tenant.tenant_code}",
                encrypted_sip_password=SecretService.encrypt(generate_temp_password()),
                user=user,
            )

            if account_name and department_name:
                did_number = next(
                    (n for n, d, a in DID_DEPARTMENT_ACCOUNT if d == department_name and a == account_name),
                    None,
                )
                did = DID.objects.filter(tenant=tenant, number=did_number).first() if did_number else None
                if did:
                    UserDIDDivisionAssignment.objects.get_or_create(
                        user=user, did=did, division=division_dental, department=did.department,
                    )

            created_users.append(email)
            self.stdout.write(self.style.SUCCESS(f"  created {email} ext={ext_number}"))

        return created_users
