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

from typing import Optional

from apps.dids.models import DID, TLGroupAccess, UserDIDDivisionAssignment


def _tl_entries(user):
    """
    Returns the raw (did_id, department_id, division_id) rows across all of
    a user's TLGroupAccess grants. Each AccessGroupEntry names either a did
    or a department (never both) — see AccessGroupEntry docstring.
    """
    return list(
        TLGroupAccess.objects.filter(user=user).values_list(
            "group__entries__did_id",
            "group__entries__department_id",
            "group__entries__division_id",
        )
    )


def _expand_did_ids(did_id, department_id) -> set:
    """Resolves one entry's (did_id, department_id) to concrete DID ids."""
    if did_id is not None:
        return {did_id}
    if department_id is not None:
        return set(DID.objects.filter(department_id=department_id).values_list("id", flat=True))
    return set()


def resolve_tl_extensions(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of extension numbers
    they are permitted to see in call logs.

    CDR records carry extension_number, not division — division only exists
    on UserDIDDivisionAssignment (the caller's work assignment). So a TL's
    (DID or Department, Division) grant is resolved by finding every caller
    assigned to a matching DID (and, if the grant specifies one, that
    division), then collecting their extension numbers. A department-scoped
    entry expands to every DID currently under it.

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

    entries = _tl_entries(user)

    extensions: set = set()
    for did_id, department_id, division_id in entries:
        did_ids = _expand_did_ids(did_id, department_id)
        if not did_ids:
            continue
        qs = UserDIDDivisionAssignment.objects.filter(did_id__in=did_ids).select_related(
            "user__extension"
        )
        if division_id is not None:
            qs = qs.filter(division_id=division_id)
        for assignment in qs:
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
    to every DID currently under it.

    Returns None if the user has no TLGroupAccess grants (scoping does not
    apply); returns an empty set if grants exist but resolve to zero DIDs.
    """
    entries = _tl_entries(user)
    if not entries:
        return None

    did_ids: set = set()
    for did_id, department_id, _division_id in entries:
        did_ids |= _expand_did_ids(did_id, department_id)
    return did_ids


def resolve_tl_department_ids(user) -> Optional[set]:
    """
    Resolves a user's TLGroupAccess grants into the set of Department ids
    they are explicitly granted (i.e. entries with department set,
    regardless of did). Does NOT include departments merely implied by a
    DID-scoped entry — this is for callers that need to know which
    departments were granted wholesale (e.g. a departments listing).

    Returns None if the user has no TLGroupAccess grants; returns an empty
    set if grants exist but none of them are department-scoped.
    """
    grants = list(
        TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
    )
    if not grants:
        return None

    department_ids = list(
        TLGroupAccess.objects.filter(user=user).values_list(
            "group__entries__department_id", flat=True
        )
    )
    return {department_id for department_id in department_ids if department_id is not None}


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
    grants = list(
        TLGroupAccess.objects.filter(user=user).values_list("group_id", flat=True)
    )
    if not grants:
        return None

    entries = list(
        TLGroupAccess.objects.filter(user=user).values_list(
            "group__entries__division_id", flat=True
        )
    )
    if any(division_id is None for division_id in entries):
        return None
    return {division_id for division_id in entries if division_id is not None}


def is_scoped_team_lead(user) -> bool:
    """True when this user's visibility must be restricted to their
    TLGroupAccess grants (i.e. they have at least one grant and are not a
    superadmin)."""
    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        return False
    return TLGroupAccess.objects.filter(user=user).exists()
