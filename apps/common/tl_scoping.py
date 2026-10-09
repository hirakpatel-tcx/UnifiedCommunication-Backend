"""
apps/common/tl_scoping.py
──────────────────────────
Shared Team Lead (TL) visibility scoping.

A Team Lead is granted read-only log/report visibility into a set of
(DID, Division) combinations via TLGroupAccess → AccessGroup →
AccessGroupEntry. This module resolves those grants into the concrete
extension numbers and DID ids a TL is permitted to see, so every listing
that exposes caller-identifying data (CDR logs, extensions, DIDs) applies
the same restriction consistently.

Superadmins and admins without TL grants are unrestricted; this module is
only consulted for callers who do have TLGroupAccess grants.
"""

from collections import defaultdict
from typing import Optional

from django.db.models import Q

from apps.dids.models import DID, TLGroupAccess, UserDIDDivisionAssignment


def _tl_entries(user):
    """
    Returns the raw (did_id, did__department_id, department_id, division_id)
    rows across all of a user's TLGroupAccess grants in a single query.
    did__department_id is the department of a DID-scoped entry (may be None
    if the DID has no department set); department_id is from an explicit
    department-scoped entry.
    """
    return list(
        TLGroupAccess.objects.filter(user=user).values_list(
            "group__entries__did_id",
            "group__entries__did__department_id",
            "group__entries__department_id",
            "group__entries__division_id",
        )
    )


def _tl_did_and_dept_ids(entries, tenant_id=None):
    """
    From _tl_entries rows, returns (did_ids, dept_did_ids, explicit_dept_ids):
    - did_ids: DID ids from DID-scoped entries
    - dept_did_ids: DID ids expanded from department-scoped entries (tenant-scoped)
    - explicit_dept_ids: department ids from explicit department-scoped entries
    All DID lookups are tenant-scoped when tenant_id is provided to prevent
    cross-tenant data leakage.
    """
    direct_did_ids: set = set()
    explicit_dept_ids: set = set()
    dept_ids_to_expand: set = set()

    for did_id, _did_dept_id, department_id, _division_id in entries:
        if did_id is not None:
            direct_did_ids.add(did_id)
        if department_id is not None:
            explicit_dept_ids.add(department_id)
            dept_ids_to_expand.add(department_id)

    dept_did_ids: set = set()
    if dept_ids_to_expand:
        dept_qs = DID.objects.filter(department_id__in=dept_ids_to_expand)
        if tenant_id is not None:
            dept_qs = dept_qs.filter(tenant_id=tenant_id)
        dept_did_ids = set(dept_qs.values_list("id", flat=True))

    return direct_did_ids, dept_did_ids, explicit_dept_ids


def resolve_tl_extensions(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of extension numbers
    they are permitted to see in call logs.

    CDR records carry extension_number, not division — division only exists
    on UserDIDDivisionAssignment (the caller's work assignment). So a TL's
    (DID or Department, Division) grant is resolved by finding every caller
    assigned to a matching DID (and, if the grant specifies one, that
    division), then collecting their extension numbers. A department-scoped
    entry expands to every DID currently under it (tenant-scoped to prevent
    cross-tenant leakage).

    Returns None if the user has no TLGroupAccess grants at all (i.e. this
    scoping does not apply to them and callers should fall back to their
    existing tenant-wide behavior). Returns an empty set if the user has
    grants but they resolve to zero extensions (e.g. no one is assigned yet).
    """
    entries = _tl_entries(user)
    if not entries:
        return None

    tenant_id = getattr(user, "tenant_id", None)
    direct_did_ids, dept_did_ids, _ = _tl_did_and_dept_ids(entries, tenant_id=tenant_id)

    # Build per-division filters: group by division_id so we can issue one
    # batched UserDIDDivisionAssignment query instead of N queries.
    division_to_dids: dict = defaultdict(set)  # None key = unrestricted division
    for did_id, _did_dept_id, department_id, division_id in entries:
        if did_id is not None:
            division_to_dids[division_id].add(did_id)
        if department_id is not None:
            division_to_dids[division_id].update(dept_did_ids)

    if not division_to_dids:
        return set()

    # Build a combined Q across all (did_ids, division_id) pairs.
    combined = Q()
    for division_id, did_ids in division_to_dids.items():
        if not did_ids:
            continue
        q = Q(did_id__in=did_ids)
        if division_id is not None:
            q &= Q(division_id=division_id)
        combined |= q

    assignments = (
        UserDIDDivisionAssignment.objects
        .filter(combined)
        .select_related("user__extension")
    )
    extensions: set = set()
    for assignment in assignments:
        ext = getattr(assignment.user, "extension", None)
        if ext and ext.extension_number:
            extensions.add(ext.extension_number)

    return extensions


def resolve_tl_did_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of DID ids they are
    permitted to see. Unlike extensions, DID visibility does not depend on
    division — a grant naming any division for a DID (or department) still
    means the TL may see that DID exists. A department-scoped entry expands
    to every DID currently under it (tenant-scoped to prevent cross-tenant
    leakage).

    Returns None if the user has no TLGroupAccess grants (scoping does not
    apply); returns an empty set if grants exist but resolve to zero DIDs.
    """
    entries = _tl_entries(user)
    if not entries:
        return None

    tenant_id = getattr(user, "tenant_id", None)
    direct_did_ids, dept_did_ids, _ = _tl_did_and_dept_ids(entries, tenant_id=tenant_id)
    return direct_did_ids | dept_did_ids


def resolve_tl_department_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of Department ids
    they are permitted to see: explicitly department-scoped entries AND the
    department of each DID-scoped entry's DID (read from the join, no extra
    query).

    Returns None if the user has no TLGroupAccess grants; returns an empty
    set if grants exist but resolve to zero departments.
    """
    entries = _tl_entries(user)
    if not entries:
        return None

    department_ids: set = set()
    for did_id, did_dept_id, department_id, _division_id in entries:
        if department_id is not None:
            department_ids.add(department_id)
        # did__department_id gives the department of a DID-scoped entry without
        # an extra query — already joined in _tl_entries.
        if did_id is not None and did_dept_id is not None:
            department_ids.add(did_dept_id)

    return department_ids


def resolve_tl_division_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of Division ids they
    are permitted to see.

    A null division on an AccessGroupEntry means "all divisions on this
    DID/Department" — if any of the user's grants include such an entry,
    division visibility is unrestricted and this returns None (same "no
    scoping applies" convention as resolve_tl_extensions/resolve_tl_did_ids).
    Otherwise returns the concrete set of division ids named across all
    grants. Returns None (not an empty set) when the user has no grants at
    all, since scoping only applies to users who actually have grants.
    """
    entries = _tl_entries(user)
    if not entries:
        return None

    division_ids: set = set()
    for _did_id, _did_dept_id, _department_id, division_id in entries:
        if division_id is None:
            return None  # unrestricted — at least one "all divisions" grant
        division_ids.add(division_id)
    return division_ids


def is_scoped_team_lead(user) -> bool:
    """True when this user's visibility must be restricted to their
    TLGroupAccess grants (i.e. they have at least one grant and are not a
    superadmin)."""
    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        return False
    return TLGroupAccess.objects.filter(user=user).exists()
