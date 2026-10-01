"""
apps/dids/models.py
────────────────────
DID (Direct Inward Dial) / Phone Number models.

Design decisions:

DID OWNERSHIP vs ASSIGNMENT:
  - DID belongs to a Tenant. The tenant owns the number.
  - A User is ASSIGNED access to a DID via the UserDID through table.
  - Assignment does NOT transfer ownership.
  - Multiple users can be assigned the same DID if business rules allow.

  Example:
    Tenant A
       └── DID +18321234567  (owned by Tenant A)
              ├── User A     (assigned — UserDID record)
              └── User B     (assigned — UserDID record)

FAX:
  - DID has NO fax capability. There is NO fax_enabled field.
  - Fax is represented solely by FaxBox (User.fax_boxes JSONField).
  - DID supports only: calling, messaging.

CONSTRAINTS:
  - (tenant, freeswitch_object_id) is unique — FreeSWITCH IDs are not
    globally unique; they are only unique within a tenant.

REASSIGNMENT:
  - DID can be removed and re-added to users via DIDService.
  - DIDService enforces that did.tenant == user.tenant before assigning.
"""

from django.db import models

from apps.common.models import TimestampedModel


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class Department(TimestampedModel):
    """
    Top-level organizational grouping of DIDs, scoped to a Tenant.

    Hierarchy: Department → DIDs → Divisions.
    A Department groups a set of DIDs (e.g. "Medical" → DIDs 1..n); each
    DID in turn has callers working it under one or more Divisions (tracked
    on UserDIDDivisionAssignment, unchanged by this model).

    Department is also a first-class TL log-visibility scoping unit: an
    AccessGroupEntry may grant a TL an entire Department (all DIDs under
    it) instead of naming a single DID — see AccessGroupEntry.department.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="departments",
        help_text="The tenant this department belongs to.",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text=(
            "Optional short display code/number for this department "
            "(e.g. '1', 'D-100'), unique within the tenant. Purely a "
            "display/reference label — not used for lookups."
        ),
    )

    class Meta:
        db_table = "departments"
        verbose_name = "Department"
        verbose_name_plural = "Departments"
        ordering = ["tenant", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="uq_department_tenant_name",
            ),
            models.UniqueConstraint(
                fields=["tenant", "code"],
                name="uq_department_tenant_code",
                condition=models.Q(code__gt=""),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.tenant.tenant_code})"

    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name!r} tenant={self.tenant_id}>"


# ---------------------------------------------------------------------------
# DID
# ---------------------------------------------------------------------------

class DID(TimestampedModel):
    """
    FreeSWITCH-managed phone number (DID), mirrored in Django.

    FreeSWITCH is the source of truth for DID state.
    Django mirrors the minimum state required for:
      - user assignment tracking
      - calling/messaging capability routing
      - realtime event routing

    Webhook sync:
      did.created — call FreeSWITCH API → create/sync local record (PLACEHOLDER)
      did.updated — call FreeSWITCH API → sync + update assignments (PLACEHOLDER)
      did.deleted — DO NOT call FreeSWITCH → remove assignments → delete record

    NEVER has a fax_enabled field. Fax capability is via FaxBox only.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="dids",
        help_text="The tenant that OWNS this phone number.",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dids",
        help_text=(
            "The department this DID is grouped under (optional). "
            "Must belong to the same tenant as the DID."
        ),
    )
    freeswitch_object_id = models.CharField(
        max_length=255,
        help_text=(
            "The object_id assigned by FreeSWITCH to this DID. "
            "Unique within a tenant — not globally unique."
        ),
    )
    number = models.CharField(
        max_length=20,
        db_index=True,
        help_text=(
            "Phone number in E.164 format (e.g. '+18321234567'). "
            "Stored as provided by FreeSWITCH."
        ),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable name or label for this DID (e.g. 'Main Line', 'Support').",
    )
    account = models.ForeignKey(
        "Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dids",
        help_text="The billing/client account this DID is grouped under (optional).",
    )

    class Meta:
        db_table = "dids"
        verbose_name = "DID"
        verbose_name_plural = "DIDs"
        ordering = ["tenant", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "freeswitch_object_id"],
                name="uq_did_tenant_object_id",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "number"],
                name="idx_did_tenant_number",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number} ({self.tenant.tenant_code})"

    def __repr__(self) -> str:
        return (
            f"<DID id={self.id} number={self.number!r} "
            f"tenant={self.tenant_id}>"
        )


# ---------------------------------------------------------------------------
# UserDID — through table
# ---------------------------------------------------------------------------

class UserDID(TimestampedModel):
    """
    Assignment record: grants a User access to a DID.

    This represents ACCESS, not ownership.
    The DID is still owned by the Tenant.

    Constraints:
      - (user, did) is unique — a user cannot be assigned the same DID twice.
      - DIDService enforces that user.tenant == did.tenant before creating
        a UserDID record. This constraint is not enforced at the DB level
        (it would require a cross-table check), so service-layer enforcement
        is mandatory.

    Multiple users can share the same DID:
      DID X → User A  (UserDID record)
      DID X → User B  (UserDID record)
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="user_dids",
        help_text="The user being granted access to this DID.",
    )
    did = models.ForeignKey(
        DID,
        on_delete=models.CASCADE,
        related_name="user_dids",
        help_text="The DID the user is being granted access to.",
    )

    class Meta:
        db_table = "user_dids"
        verbose_name = "User DID Assignment"
        verbose_name_plural = "User DID Assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "did"],
                name="uq_user_did_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_userdid_user"),
            models.Index(fields=["did"], name="idx_userdid_did"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.did.number}"

    def __repr__(self) -> str:
        return (
            f"<UserDID id={self.id} "
            f"user={self.user_id} did={self.did_id}>"
        )


# ---------------------------------------------------------------------------
# Division
# ---------------------------------------------------------------------------

class Division(TimestampedModel):
    """
    Global lookup of RCM divisions (e.g. EVBV, AR, Billing, Credentialing).

    Not tied to a Tenant or DID — the same division names are shared across
    clients. A caller's work assignment (which DID(s) they work, under
    which division) is tracked on UserDIDDivisionAssignment.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "divisions"
        verbose_name = "Division"
        verbose_name_plural = "Divisions"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

class Account(TimestampedModel):
    """
    Global lookup of client/billing accounts a DID can be grouped under
    (e.g. Rockwell, Perry Ave, Parcare, General).

    Not tied to a Tenant — the same account names are shared across
    clients. A DID may optionally belong to one Account (see DID.account).
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "accounts"
        verbose_name = "Account"
        verbose_name_plural = "Accounts"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# UserDIDDivisionAssignment — caller work assignment
# ---------------------------------------------------------------------------

class UserDIDDivisionAssignment(TimestampedModel):
    """
    Records which division (and, optionally, which department) a caller
    works under for a given DID.

    A caller (User) can be assigned to multiple DIDs, and each assignment
    declares the division the caller works under for that DID
    (e.g. User A / DID 1 / EVBV, User A / DID 2 / EVBV, User B / DID 1 / AR).

    A DID has a single DID.department, but the same DID can be worked by
    different callers under different departments (e.g. one caller handles
    DID X for "AR", another handles DID X for "EV"). The optional
    department field here lets an assignment override DID.department for
    that specific caller; leaving it null means "use DID.department".

    This is separate from UserDID (which only grants raw DID access) because
    the division is required here to support CDR log scoping — a TL's
    AccessGroup grants visibility by (DID, Division), and this table is what
    resolves that back to the set of extensions who actually worked under
    that DID/division combination.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="did_division_assignments",
        help_text="The caller being assigned to work this DID under this division.",
    )
    did = models.ForeignKey(
        DID,
        on_delete=models.CASCADE,
        related_name="user_division_assignments",
        help_text="The DID the caller works.",
    )
    division = models.ForeignKey(
        Division,
        on_delete=models.CASCADE,
        related_name="user_did_assignments",
        help_text="The division the caller works under for this DID.",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_did_division_assignments",
        help_text=(
            "The department this caller works this DID under (optional). "
            "Leave blank to use the DID's own department."
        ),
    )

    class Meta:
        db_table = "user_did_division_assignments"
        verbose_name = "User DID Division Assignment"
        verbose_name_plural = "User DID Division Assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "did", "division", "department"],
                name="uq_user_did_division_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_uddassign_user"),
            models.Index(fields=["did"], name="idx_uddassign_did"),
            models.Index(fields=["division"], name="idx_uddassign_division"),
            models.Index(fields=["department"], name="idx_uddassign_department"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.did.number} ({self.division.name})"

    def __repr__(self) -> str:
        return (
            f"<UserDIDDivisionAssignment id={self.id} "
            f"user={self.user_id} did={self.did_id} division={self.division_id}>"
        )


# ---------------------------------------------------------------------------
# AccessGroup — reusable log-visibility bundle (DID + Division)
# ---------------------------------------------------------------------------

class AccessGroup(TimestampedModel):
    """
    A reusable, named bundle of (DID, Division) report-scope rules.

    An AccessGroup does NOT represent DIDs assigned to any particular TL —
    it is purely a log-visibility scope definition (e.g. "Clinic 1 - Full",
    "Clinic 2 - AR Only") that can be granted to one or more TLs via
    TLGroupAccess. It has no bearing on caller work assignment.
    """

    name = models.CharField(max_length=150, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "access_groups"
        verbose_name = "Access Group"
        verbose_name_plural = "Access Groups"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class AccessGroupEntry(TimestampedModel):
    """
    One log-visibility rule inside an AccessGroup, in one of two shapes:

      - DID-scoped:        did is set, department is null.
                            division nullable — null means "all divisions on
                            this DID".
      - Department-scoped: department is set, did is null. Grants every
                            DID under that department (present now and
                            added later). division nullable — null means "all
                            divisions on every DID in this department".

    Exactly one of (did, department) must be set — enforced in
    AccessGroupEntrySerializer.validate() (a DB-level XOR CHECK constraint
    is avoided here since it can't easily express "not both null").
    """

    group = models.ForeignKey(
        AccessGroup,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    did = models.ForeignKey(
        DID,
        on_delete=models.CASCADE,
        related_name="access_group_entries",
        null=True,
        blank=True,
        help_text="The single DID this rule grants. Mutually exclusive with department.",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="access_group_entries",
        null=True,
        blank=True,
        help_text=(
            "The department this rule grants (all DIDs under it, including "
            "ones added later). Mutually exclusive with did."
        ),
    )
    division = models.ForeignKey(
        Division,
        on_delete=models.CASCADE,
        related_name="access_group_entries",
        null=True,
        blank=True,
        help_text="Leave blank to grant all divisions on the DID(s) named above.",
    )

    class Meta:
        db_table = "access_group_entries"
        verbose_name = "Access Group Entry"
        verbose_name_plural = "Access Group Entries"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "did", "division"],
                name="uq_access_group_entry",
            ),
            models.UniqueConstraint(
                fields=["group", "department", "division"],
                name="uq_access_group_entry_department",
            ),
        ]
        indexes = [
            models.Index(fields=["group"], name="idx_agentry_group"),
            models.Index(fields=["did"], name="idx_agentry_did"),
            models.Index(fields=["department"], name="idx_agentry_department"),
        ]

    def __str__(self) -> str:
        division = self.division.name if self.division_id else "All Divisions"
        scope = self.did.number if self.did_id else f"Department: {self.department.name}"
        return f"{self.group.name}: {scope} / {division}"


# ---------------------------------------------------------------------------
# TLGroupAccess — grants a TL (any User) visibility into an AccessGroup
# ---------------------------------------------------------------------------

class TLGroupAccess(TimestampedModel):
    """
    Grants a User (typically a Team Lead) read-only log/report visibility
    into everything defined by an AccessGroup.

    This is strictly a reporting grant — it never implies work assignment,
    and is unrelated to UserDID / UserDIDDivisionAssignment.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="tl_group_access",
        help_text="The Team Lead being granted log visibility.",
    )
    group = models.ForeignKey(
        AccessGroup,
        on_delete=models.CASCADE,
        related_name="tl_access_grants",
    )

    class Meta:
        db_table = "tl_group_access"
        verbose_name = "TL Group Access"
        verbose_name_plural = "TL Group Access"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group"],
                name="uq_tl_group_access",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_tlaccess_user"),
            models.Index(fields=["group"], name="idx_tlaccess_group"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.group.name}"
