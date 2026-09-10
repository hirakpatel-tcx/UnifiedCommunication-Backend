"""
apps/common/services/fax_tag_service.py
─────────────────────────────────────────
Merges local FaxTag workflow metadata (assigned user + review status) onto
proxied FreeSWITCH fax transmission records, keyed by fax_file_uuid.
"""

from apps.common.models import FaxTag

# Keys FreeSWITCH fax records may carry the transmission UUID under.
_FAX_FILE_UUID_FIELDS = ("fax_file_uuid", "uuid", "id")


def _record_fax_file_uuid(record: dict):
    return next((record.get(f) for f in _FAX_FILE_UUID_FIELDS if record.get(f)), None)


def annotate_fax_tags(tenant, records: list) -> list:
    """
    Mutates a list of dict fax records in place, adding `assigned_to` (user
    id + name + extension) and `status` keys sourced from FaxTag. Records
    with no FaxTag row get assigned_to=None, status="pending" (the default
    workflow state).
    """
    uuids = [_record_fax_file_uuid(r) for r in records if isinstance(r, dict)]
    uuids = [u for u in uuids if u]
    if not uuids:
        return records

    tags = FaxTag.objects.filter(tenant=tenant, fax_file_uuid__in=uuids).select_related(
        "assigned_to", "assigned_to__extension"
    )
    lookup = {t.fax_file_uuid: t for t in tags}

    for record in records:
        if not isinstance(record, dict):
            continue
        fax_file_uuid = _record_fax_file_uuid(record)
        tag_obj = lookup.get(fax_file_uuid)

        record["status"] = tag_obj.status if tag_obj else "pending"

        assigned_user = tag_obj.assigned_to if tag_obj else None
        if assigned_user:
            extension = getattr(assigned_user, "extension", None)
            name = f"{assigned_user.first_name} {assigned_user.last_name}".strip() or assigned_user.email
            record["assigned_to"] = {
                "user_id": str(assigned_user.id),
                "name": name,
                "extension_number": extension.extension_number if extension else None,
            }
        else:
            record["assigned_to"] = None

    return records
