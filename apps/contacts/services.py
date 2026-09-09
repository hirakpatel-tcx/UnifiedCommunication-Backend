"""
apps/contacts/services.py
──────────────────────────
Helpers for annotating externally-sourced records (CDR, voicemail) that carry
a raw phone number with whether that number matches a saved Contact.

CDR and voicemail data are never persisted in this backend (FreeSWITCH is the
source of truth), so matching happens on read: given the numbers appearing in
a page of proxied results, resolve them against ContactNumber in one query.
"""

import re

from apps.contacts.models import ContactNumber

_DIGITS_RE = re.compile(r"\D+")


def _match_key(raw_number) -> str:
    """Last 10 digits of a number, used as a formatting-tolerant match key."""
    if not raw_number:
        return ""
    digits = _DIGITS_RE.sub("", str(raw_number))
    return digits[-10:] if len(digits) >= 10 else digits


def build_contact_lookup(tenant, numbers) -> dict:
    """
    Given a tenant and an iterable of raw phone numbers, returns a dict mapping
    each input number (as given) to {"contact_saved": True, "contact_id": "..."}
    for numbers that match a saved Contact, for use when annotating records.

    Numbers with no match are simply absent from the returned dict.
    """
    keys = {}
    for raw_number in numbers:
        key = _match_key(raw_number)
        if key:
            keys.setdefault(key, set()).add(raw_number)

    if not keys:
        return {}

    candidates = ContactNumber.objects.filter(
        contact__tenant=tenant,
    ).values_list("number", "contact_id")

    result = {}
    for stored_number, contact_id in candidates:
        key = _match_key(stored_number)
        for raw_number in keys.get(key, ()):
            result.setdefault(raw_number, {"contact_saved": True, "contact_id": str(contact_id)})

    return result


def annotate_contact_flags(tenant, records, number_field, out_saved="contact_saved", out_id="contact_id"):
    """
    Mutates a list of dict records in place, adding `out_saved`/`out_id` keys
    based on whether the record's counterparty number matches a saved Contact
    for tenant. Records without a match get contact_saved=False, contact_id=None.

    `number_field` is either a fixed field name (str) applied to every record,
    or a callable(record) -> field name, for cases where which field holds the
    "other party" number depends on the record itself (e.g. CDR direction).
    """
    get_field = number_field if callable(number_field) else (lambda r: number_field)

    numbers = [r.get(get_field(r)) for r in records if isinstance(r, dict)]
    lookup = build_contact_lookup(tenant, numbers)

    for record in records:
        if not isinstance(record, dict):
            continue
        match = lookup.get(record.get(get_field(record)))
        record[out_saved] = bool(match)
        record[out_id] = match["contact_id"] if match else None

    return records
