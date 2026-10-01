"""
apps/users/permissions.py
──────────────────────────
Curated list of resources shown on the admin permissions grid (CREATE /
EDIT / DELETE / VIEW per resource), and helpers to resolve it against
Django's built-in per-model Permission rows.

This replaces the previous behavior of listing every Django model as a
"resource" (raw ContentType dump) with a fixed, meaningful set an admin can
actually be granted rights on.
"""

# (app_label, model_name, display_name) — model_name must match the lower-
# cased model class name Django uses for its auto-generated permissions
# (add_<model_name> / change_<model_name> / delete_<model_name> /
# view_<model_name>).
CURATED_RESOURCES = [
    ("users", "user", "User"),
    ("contacts", "contact", "Contact"),
    ("dids", "department", "Department"),
    ("dids", "division", "Division"),
    ("dids", "account", "Account"),
    ("dids", "tlgroupaccess", "Log Access (Team Lead)"),
]

# Division/Account are create/view only in the admin grid — editing/deleting
# a shared lookup value is a superadmin-only action.
RESOURCE_ACTIONS = {
    "division": ("add", "view"),
    "account": ("add", "view"),
}
DEFAULT_ACTIONS = ("add", "change", "delete", "view")


def resource_actions(model_name: str) -> tuple:
    return RESOURCE_ACTIONS.get(model_name, DEFAULT_ACTIONS)
