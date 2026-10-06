"""
apps/users/validators.py
────────────────────────
Validators for User model JSONFields.

fax_boxes — exact required structure per architecture:
    [
        {
            "fax_uuid":             "978c7337-d642-4cd7-a38a-d0a61c2cfbde",
            "fax_caller_id_name":   "Hirak",
            "fax_caller_id_number": "+13468310766"
        }
    ]

voicemail_boxes — exact required structure:
    [101, 1001]

Security note: These validators run at the application/model-validation layer.
They do NOT verify that the fax_uuid or voicemail_box_id exists in FreeSWITCH.
That verification is the responsibility of FaxBoxService and VoicemailBoxService,
which call FreeSWITCH to confirm tenant ownership before writing to the JSON field.
"""

from django.core.exceptions import ValidationError

from apps.tenants.models import VALID_FEATURE_KEYS


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

def validate_user_features(value: dict) -> None:
    """
    Validates the structure of User.features.

    Rules:
    - Must be a dict.
    - Keys must be a subset of VALID_FEATURE_KEYS (calling, messaging, fax).
    - Values must be booleans.
    - Empty dict is valid (means inherit everything from tenant).
    """
    if not isinstance(value, dict):
        raise ValidationError(
            "features must be a JSON object.",
            code="features_not_dict",
        )

    unknown_keys = set(value.keys()) - VALID_FEATURE_KEYS
    if unknown_keys:
        raise ValidationError(
            f"features contains unknown keys: {sorted(unknown_keys)}. "
            f"Valid keys are: {sorted(VALID_FEATURE_KEYS)}.",
            code="features_unknown_keys",
        )

    for key, val in value.items():
        if not isinstance(val, bool):
            raise ValidationError(
                f"features.{key} must be a boolean, got {type(val).__name__}.",
                code="features_invalid_value",
            )


# ---------------------------------------------------------------------------
# fax_boxes
# ---------------------------------------------------------------------------

_FAX_BOX_REQUIRED_KEYS = frozenset(
    {"fax_uuid", "fax_caller_id_name", "fax_caller_id_number"}
)


def validate_fax_boxes(value: list) -> None:
    """
    Validates the structure of User.fax_boxes.

    Rules:
    - Must be a list.
    - Each element must be a dict.
    - Each dict must contain exactly the required keys.
    - fax_uuid must be a non-empty string.
    - fax_caller_id_name must be a string.
    - fax_caller_id_number must be a non-empty string.
    - fax_uuid must be unique within the list (no duplicate FaxBox assignments).
    """
    if not isinstance(value, list):
        raise ValidationError(
            "fax_boxes must be a JSON array.",
            code="fax_boxes_not_list",
        )

    seen_uuids: set[str] = set()

    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValidationError(
                f"fax_boxes[{idx}] must be a JSON object (dict), "
                f"got {type(item).__name__}.",
                code="fax_box_not_dict",
            )

        missing = _FAX_BOX_REQUIRED_KEYS - set(item.keys())
        if missing:
            raise ValidationError(
                f"fax_boxes[{idx}] is missing required keys: "
                f"{sorted(missing)}.",
                code="fax_box_missing_keys",
            )

        extra = set(item.keys()) - _FAX_BOX_REQUIRED_KEYS
        if extra:
            raise ValidationError(
                f"fax_boxes[{idx}] contains unexpected keys: "
                f"{sorted(extra)}.",
                code="fax_box_extra_keys",
            )

        fax_uuid = item["fax_uuid"]
        if not isinstance(fax_uuid, str) or not fax_uuid.strip():
            raise ValidationError(
                f"fax_boxes[{idx}].fax_uuid must be a non-empty string.",
                code="fax_box_invalid_uuid",
            )

        name = item["fax_caller_id_name"]
        if not isinstance(name, str):
            raise ValidationError(
                f"fax_boxes[{idx}].fax_caller_id_name must be a string, "
                f"got {type(name).__name__}.",
                code="fax_box_invalid_name",
            )

        number = item["fax_caller_id_number"]
        if not isinstance(number, str) or not number.strip():
            raise ValidationError(
                f"fax_boxes[{idx}].fax_caller_id_number must be a non-empty string.",
                code="fax_box_invalid_number",
            )

        if fax_uuid in seen_uuids:
            raise ValidationError(
                f"fax_boxes contains duplicate fax_uuid: {fax_uuid!r}.",
                code="fax_box_duplicate_uuid",
            )
        seen_uuids.add(fax_uuid)


# ---------------------------------------------------------------------------
# voicemail_boxes
# ---------------------------------------------------------------------------

def validate_voicemail_boxes(value: list) -> None:
    """
    Validates the structure of User.voicemail_boxes.

    Rules:
    - Must be a list.
    - Each element must be a non-negative integer.
    - isinstance(x, bool) is excluded — booleans are a subclass of int in Python.
    - Values must be unique within the list (no duplicate box assignments).
    """
    if not isinstance(value, list):
        raise ValidationError(
            "voicemail_boxes must be a JSON array.",
            code="voicemail_boxes_not_list",
        )

    seen_ids: set[int] = set()

    for idx, item in enumerate(value):
        # Booleans are a subclass of int in Python — explicitly reject them.
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValidationError(
                f"voicemail_boxes[{idx}] must be an integer, "
                f"got {type(item).__name__}.",
                code="voicemail_box_not_int",
            )
        if item < 0:
            raise ValidationError(
                f"voicemail_boxes[{idx}] must be a non-negative integer, got {item}.",
                code="voicemail_box_negative",
            )
        if item in seen_ids:
            raise ValidationError(
                f"voicemail_boxes contains duplicate ID: {item}.",
                code="voicemail_box_duplicate",
            )
        seen_ids.add(item)
