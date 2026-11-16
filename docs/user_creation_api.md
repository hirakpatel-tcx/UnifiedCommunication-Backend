# User Creation — Django Admin flow and the API equivalent for the frontend

Goal: the frontend "Add user" form should behave the same as **Django admin → Users → Add user**.
This document describes (1) what the admin does today, (2) the API calls that reproduce it, with payloads and responses, and (3) the places where the admin and the API **do not match yet**.

Auth for every call: `Authorization: Bearer <JWT>`. Base path: `/api/v1`.

---

## 1. What the Django admin does today

Source: `apps/users/admin.py` (`UserAdmin`, `UserCreationFormForCustomUser`, `UserFeaturesFormMixin`).

### Add-user form fields (in order)
| Field | Type | Notes |
|---|---|---|
| Email | email, required | Login identity. **Globally unique across all tenants.** Lower-cased and trimmed on save. |
| First name / Last name | text, optional | Max 150 chars each. |
| Tenant | dropdown | Which tenant the user belongs to. Required for every non-platform user. |
| Role | dropdown | `user` (default), `admin`, `superadmin`. |
| SIP domain | text, optional | Per-user override. Blank = inherit the tenant's SIP domain. |
| Extension | searchable dropdown, optional | Only **unassigned** extensions are offered. |
| Calling / Messaging / Fax | dropdown each | `Inherit from tenant` (default), `True`, `False`. Stored in `User.features`; "Inherit" omits the key. The tenant is the ceiling: `True` cannot enable something the tenant has disabled. |
| Staff / Superuser / Active | checkboxes | `is_active` defaults to true. `is_staff` / `is_superuser` give Django-admin access (operators only). |

There is **no password field**. On first save the admin always:
1. generates a temporary password (14 chars, at least one lower, upper, digit and one of `!@#$%^&*`),
2. sets `must_change_password = true` (and `is_first_login = true`),
3. assigns the chosen extension (frees any previously assigned one).

### Two save buttons
- **Save** — creates the user. **No email is sent**, and nobody knows the temp password, so the user cannot log in until someone uses Re-invite or a password reset.
- **Save and send invite email** — creates the user and queues the welcome email with the temp password.

### Only available *after* creation (change page)
Team Lead flag, Fax boxes (raw JSON), Voicemail boxes (raw JSON), DID assignments with division/department (inlines), group/permission assignment, and the **Resend invite** button.

### What the user receives
Welcome email ("Welcome to TCX Connect — your account is ready") with their email, the temp password and a desktop-app download link. At first login they must change the password.

---

## 2. Frontend flow using the API

### Step A — load dropdown data
| Dropdown | Call | Who can call |
|---|---|---|
| Tenant | `GET /tenants/?search=&is_active=true` | superadmin only |
| Unassigned extensions | `GET /extensions/?is_assigned=false&search=101` (superadmin adds `&tenant_id=<uuid>` or header `X-Tenant-ID`) | admin, superadmin |
| DIDs | `GET /dids/?search=555` | admin, superadmin |
| Divisions | `GET /dids/divisions/` | admin, superadmin |
| Departments | `GET /dids/departments/` | admin, superadmin |
| Fax boxes | `GET /fax/boxes/` | any user with fax enabled |

Reload the extension and DID lists whenever the tenant changes.
Tenant admins don't need the tenant dropdown: they are always pinned to their own tenant.

### Step B — create the user
**`POST /users/invite/`** — the equivalent of the admin's "Save and send invite email". Permission: admin or superadmin.

Request:
```json
{
  "email": "jane.doe@example.com",
  "first_name": "Jane",
  "last_name": "Doe",
  "role": "user",
  "tenant_id": "b3a1c7e2-0f4d-4a6e-9d11-5c2a7e8f9a10",
  "sip_domain": "",
  "is_active": true,
  "is_team_lead": false,
  "features": {"fax": false},
  "send_invite": true,
  "extension_id": "101",
  "did_assignments": [
    {"did_id": "+15551230000", "division_id": "2f8e…", "department_id": null}
  ],
  "fax_boxes": [
    {"fax_uuid": "1b9f3c1e-6a4d-4c55-9b0e-2f6d8a7c1111",
     "fax_caller_id_name": "Acme",
     "fax_caller_id_number": "+15551230000"}
  ],
  "voicemail_boxes": [101]
}
```

| Field | Required | Notes |
|---|---|---|
| `email` | yes | Unique across all tenants (case-insensitive). |
| `first_name`, `last_name` | no | |
| `role` | no (default `user`) | `user` / `admin` / `superadmin`. Only a superadmin caller may set `superadmin`. |
| `tenant_id` | superadmin: yes, unless `role=superadmin` or the extension implies it | Accepts tenant UUID, FreeSWITCH tenant UUID or `tenant_code`. **Ignored for tenant admins** (they get their own tenant). |
| `sip_domain` | no | Blank = inherit tenant. |
| `is_active` | no (default true) | |
| `is_team_lead` | no | Not in the admin add form (admin sets it later). |
| `features` | no | Per-user overrides, same as the admin's Calling / Messaging / Fax dropdowns. Keys: `calling`, `messaging`, `fax`; values must be booleans. **A missing key = "Inherit from tenant".** Tenant is the ceiling: `true` cannot enable what the tenant has disabled. Unknown keys or non-boolean values return `400`. |
| `send_invite` | no (default `true`) | `false` = create the user **without emailing** (the admin's plain **Save**). The user can't log in until you call `POST /users/{id}/reinvite/` (or they use forgot-password). |
| `extension_id` | no | Extension UUID, FreeSWITCH id **or extension number**, resolved inside the tenant. Needs tenant `calling` enabled. Empty/`null` = none. |
| `did_assignments` | no | List of `{did_id, division_id?, department_id?}`; `did_id` may be a UUID or the phone number. Needs tenant `calling` or `messaging`. Not available in the admin add form. |
| `fax_boxes` | no | Each item needs exactly `fax_uuid`, `fax_caller_id_name` (string, may be empty), `fax_caller_id_number` (non-empty). No duplicate `fax_uuid`. Needs tenant `fax` enabled. |
| `voicemail_boxes` | no | Array of non-negative integers, no duplicates. |

There is **no** `password`, `notify` or `must_change_password` on this endpoint — the temp password is always generated; the email is sent unless `send_invite` is `false`.

Response `201 Created`:
```json
{
  "id": "6d0c…",
  "email": "jane.doe@example.com",
  "first_name": "Jane",
  "last_name": "Doe",
  "role": "user",
  "is_active": true,
  "is_team_lead": false,
  "tenant": {
    "id": "b3a1…", "freeswitch_tenant_uuid": "…", "tenant_code": "ACME",
    "tenant_name": "Acme Health", "sip_domain": "acme.pbx.example.com"
  },
  "features": {"calling": true, "messaging": true, "fax": false},
  "feature_overrides": {"fax": false},
  "invite_sent": true,
  "extension": {"id": "…", "extension_number": "101", "sip_username": "101", "sip_password": "…", "transport_type": "udp"},
  "dids": [{"id": "…", "number": "+15551230000", "name": "Main", "did_number": "+15551230000", "did_name": "Main"}],
  "divisions": [{"id": "2f8e…", "name": "Billing"}],
  "fax_boxes": [{"fax_uuid": "…", "fax_caller_id_name": "Acme", "fax_caller_id_number": "+15551230000"}],
  "voicemail_boxes": [101],
  "is_first_login": true,
  "must_change_password": true,
  "created_at": "2026-11-16T10:21:00Z"
}
```
The temporary password is **never** returned; it only goes by email.
`features` in the response is the *effective* value (tenant ceiling merged with per-user overrides). `feature_overrides` is the raw stored override set — use it to pre-fill the three dropdowns when editing (missing key = Inherit). `invite_sent` is `false` when `send_invite=false`. `invite_sent` appears on the create response only.

Common errors (`400` unless noted):
```json
{"email": ["A user with this email already exists."]}
{"tenant_id": "tenant_id is required when creating a tenant user."}
{"tenant_id": "Tenant 'XYZ' not found."}
{"extension_id": "Extension '999' not found in tenant."}
{"extension_id": "Calling feature is disabled for this tenant. Cannot assign extension."}
{"fax_boxes": "Fax feature is disabled for this tenant. Cannot assign fax boxes."}
{"fax_boxes": ["fax_boxes[0] is missing required keys: ['fax_caller_id_number']."]}
{"did_assignments": "DID '+1555…' not found in tenant."}
{"role": ["Only a superadmin can assign the superadmin role."]}
{"features": ["features contains unknown keys: ['bogus']. Valid keys are: ['calling', 'fax', 'messaging']."]}
{"features": ["features.fax must be a boolean, got str."]}
{"detail": "You do not have permission to perform this action."}   // 403, role "user"
```
The create runs in one database transaction, so a failed assignment leaves no half-created user.

### Step C — "Save" without sending an email
Send `"send_invite": false` to `/users/invite/`. The response has `"invite_sent": false`; send the email later with `POST /users/{id}/reinvite/`.

### Step D — after creation
| Action | Call | Notes |
|---|---|---|
| Resend invite | `POST /users/{id}/reinvite/` | Only while `is_first_login` is true and the user is active; issues a **new** temp password. Returns `{"status":"reinvited","email":"…"}`. `400` otherwise. |
| View a user | `GET /users/{id}/` | Same shape as the create response. |
| Edit user + resources in one call | `PATCH /users/{id}/` | Same fields as create, plus `password`, `notify`, `generate_temp_password`. Sending `did_assignments` / `fax_boxes` / `voicemail_boxes` replaces the whole list. `features` also **replaces** the stored overrides: send the full set (`{}` = inherit everything). |
| Assign / remove extension | `POST` / `DELETE /users/{id}/extension/` body `{"extension_id": "…"}` | |
| Grant / revoke DID | `POST /users/{id}/dids/` body `{"did_id": "…"}`; `DELETE /users/{id}/dids/{did_id}/` | |
| Assign / remove fax box | `POST /users/{id}/fax-boxes/` body `{"fax_uuid","fax_caller_id_name","fax_caller_id_number"}`; `DELETE /users/{id}/fax-boxes/{fax_uuid}/` | |
| Assign / remove voicemail box | `POST /users/{id}/voicemail-boxes/` body `{"voicemail_box_id": 101}`; `DELETE …/voicemail-boxes/{box_id}/` | |
| Admin permissions | `PATCH /users/{id}/permissions/` body `{"permission_ids": ["…"]}` | superadmin only |

### Alternative: `POST /users/` (manual password)
Same payload as the invite endpoint, plus `password` (**required**), optional `must_change_password` (default true). Sends the welcome email either way. Use it only if the form needs "set the password myself"; the admin form does not.

---

## 3. Who can do what
| Caller | Create user | Tenant used | Sees |
|---|---|---|---|
| superadmin | yes | `tenant_id` from the form | everyone (filter with `?tenant_id=`) |
| admin | yes | own tenant (form value ignored) | own tenant, never superadmins |
| team lead / user | no (`403`) | — | — |

---

## 4. Gaps between the admin and the API (decide before building the form)

1. **Feature overrides — fixed.** `features` is accepted on create, invite and PATCH, and the raw overrides come back as `feature_overrides`.
2. **Create without the email — fixed.** `send_invite: false` on `/users/invite/`.
3. **`is_staff` / `is_superuser` are admin-only.** Deliberately not exposed in the API — keep it that way. The frontend form should not show them.
4. **Role escalation — fixed.** Only a superadmin can assign `role=superadmin`, on create, invite and PATCH. A tenant admin who tries gets `400 {"role": ["Only a superadmin can assign the superadmin role."]}`. Re-sending an existing superadmin's unchanged role is still accepted.
5. **Extension picker for superadmins** needs the tenant first (`tenant_id` query param), because `/extensions/` is tenant-scoped.
6. **Fax box validation is structural only** — the API does not check that `fax_uuid` exists in the PBX for that tenant on create. The fax box list from `/fax/boxes/` is the only guard, so build the picker from it.

## 5. Bulk invites
Sending many welcome emails at once is discouraged. If the frontend gets a bulk-add or CSV import, create users in **small batches (about 5 at a time)** rather than all at once.
