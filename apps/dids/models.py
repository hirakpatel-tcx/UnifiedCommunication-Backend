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
# Department
# ---------------------------------------------------------------------------

class Department(TimestampedModel):
    """
    Global lookup of RCM departments (e.g. EVBV, AR, Billing).

    Not tied to a Tenant or DID — the same department names are shared
    across clients. A caller's work assignment (which DID(s) they work,
    under which department) is tracked on UserDIDDepartmentAssignment.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "departments"
        verbose_name = "Department"
        verbose_name_plural = "Departments"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# UserDIDDepartmentAssignment — caller work assignment
# ---------------------------------------------------------------------------

class UserDIDDepartmentAssignment(TimestampedModel):
    """
    Records which department a caller works under for a given DID.

    A caller (User) can be assigned to multiple DIDs, and each assignment
    declares the department the caller works under for that DID
    (e.g. User A / DID 1 / EVBV, User A / DID 2 / EVBV, User B / DID 1 / AR).

    This is separate from UserDID (which only grants raw DID access) because
    the department is required here to support CDR log scoping — a TL's
    AccessGroup grants visibility by (DID, Department), and this table is
    what resolves that back to the set of extensions who actually worked
    under that DID/department combination.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="did_department_assignments",
        help_text="The caller being assigned to work this DID under this department.",
    )
    did = models.ForeignKey(
        DID,
        on_delete=models.CASCADE,
        related_name="user_department_assignments",
        help_text="The DID the caller works.",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="user_did_assignments",
        help_text="The department the caller works under for this DID.",
    )

    class Meta:
        db_table = "user_did_department_assignments"
        verbose_name = "User DID Department Assignment"
        verbose_name_plural = "User DID Department Assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "did", "department"],
                name="uq_user_did_department_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_uddassign_user"),
            models.Index(fields=["did"], name="idx_uddassign_did"),
            models.Index(fields=["department"], name="idx_uddassign_department"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.did.number} ({self.department.name})"

    def __repr__(self) -> str:
        return (
            f"<UserDIDDepartmentAssignment id={self.id} "
            f"user={self.user_id} did={self.did_id} department={self.department_id}>"
        )


# ---------------------------------------------------------------------------
# AccessGroup — reusable log-visibility bundle (DID + Department)
# ---------------------------------------------------------------------------

class AccessGroup(TimestampedModel):
    """
    A reusable, named bundle of (DID, Department) report-scope rules.

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
    One (DID, Department) rule inside an AccessGroup.

    department is nullable: a null department means "all departments on
    this DID" within the group.
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
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="access_group_entries",
        null=True,
        blank=True,
        help_text="Leave blank to grant all departments on this DID.",
    )

    class Meta:
        db_table = "access_group_entries"
        verbose_name = "Access Group Entry"
        verbose_name_plural = "Access Group Entries"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "did", "department"],
                name="uq_access_group_entry",
            ),
        ]
        indexes = [
            models.Index(fields=["group"], name="idx_agentry_group"),
            models.Index(fields=["did"], name="idx_agentry_did"),
        ]

    def __str__(self) -> str:
        dept = self.department.name if self.department_id else "All Departments"
        return f"{self.group.name}: {self.did.number} / {dept}"


# ---------------------------------------------------------------------------
# TLGroupAccess — grants a TL (any User) visibility into an AccessGroup
# ---------------------------------------------------------------------------

class TLGroupAccess(TimestampedModel):
    """
    Grants a User (typically a Team Lead) read-only log/report visibility
    into everything defined by an AccessGroup.

    This is strictly a reporting grant — it never implies work assignment,
    and is unrelated to UserDID / UserDIDDepartmentAssignment.
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
