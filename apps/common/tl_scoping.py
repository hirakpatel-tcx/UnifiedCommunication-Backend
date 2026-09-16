"""
apps/common/tl_scoping.py
──────────────────────────
Shared Team Lead (TL) visibility scoping.

A Team Lead is granted read-only log/report visibility into a set of
(DID, Department) combinations via TLGroupAccess → AccessGroup →
AccessGroupEntry. This module resolves those grants into the concrete
extension numbers and DID ids a TL is permitted to see, so every listing
that exposes caller-identifying data (CDR logs, extensions, DIDs) applies
the same restriction consistently.

Superadmins and admins without TL grants are unrestricted; this module is
only consulted for callers who do have TLGroupAccess grants.
"""

from typing import Optional

from apps.dids.models import TLGroupAccess, UserDIDDepartmentAssignment


def resolve_tl_extensions(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of extension numbers
    they are permitted to see in call logs.

    CDR records carry extension_number, not department — department only
    exists on UserDIDDepartmentAssignment (the caller's work assignment).
    So a TL's (DID, Department) grant is resolved by finding every caller
    assigned to that DID (and, if the grant specifies one, that department),
    then collecting their extension numbers.

    Returns None if the user has no TLGroupAccess grants at all (i.e. this
    scoping does not apply to them and callers should fall back to their
    existing tenant-wide behavior). Returns an empty set if the user has
    grants but they resolve to zero extensions (e.g. no one is assigned yet).
    """
    grants = list(
        TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
    )
    if not grants:
        return None

    entries = TLGroupAccess.objects.filter(user=user).values_list(
        "group__entries__did_id", "group__entries__department_id"
    )

    extensions: set = set()
    for did_id, department_id in entries:
        if did_id is None:
            continue
        qs = UserDIDDepartmentAssignment.objects.filter(did_id=did_id).select_related(
            "user__extension"
        )
        if department_id is not None:
            qs = qs.filter(department_id=department_id)
        for assignment in qs:
            ext = getattr(assignment.user, "extension", None)
            if ext and ext.extension_number:
                extensions.add(ext.extension_number)

    return extensions


def resolve_tl_did_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of DID ids they are
    permitted to see. Unlike extensions, DID visibility does not depend on
    department — a grant naming any department for a DID still means the TL
    may see that DID exists.

    Returns None if the user has no TLGroupAccess grants (scoping does not
    apply); returns an empty set if grants exist but resolve to zero DIDs.
    """
    entries = TLGroupAccess.objects.filter(user=user).values_list(
        "group__entries__did_id", flat=True
    )
    entries = list(entries)
    if not entries:
        return None

    return {did_id for did_id in entries if did_id is not None}


def resolve_tl_department_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of Department ids
    they are permitted to see.

    A null department on an AccessGroupEntry means "all departments on this
    DID" — if any of the user's grants include such an entry, department
    visibility is unrestricted and this returns None (same "no scoping
    applies" convention as resolve_tl_extensions/resolve_tl_did_ids).
    Otherwise returns the concrete set of department ids named across all
    grants. Returns None (not an empty set) when the user has no grants at
    all, since scoping only applies to users who actually have grants.
    """
    grants = list(
        TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
    )
    if not grants:
        return None

    entries = list(
        TLGroupAccess.objects.filter(user=user).values_list(
            "group__entries__department_id", flat=True
        )
    )
    if any(department_id is None for department_id in entries):
        return None
    return {department_id for department_id in entries if department_id is not None}


def is_scoped_team_lead(user) -> bool:
    """True when this user's visibility must be restricted to their
    TLGroupAccess grants (i.e. they have at least one grant and are not a
    superadmin)."""
    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        return False
    return TLGroupAccess.objects.filter(user=user).exists()
