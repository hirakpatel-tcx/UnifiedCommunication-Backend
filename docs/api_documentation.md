# Unified Communication System — Backend API Specification

**Version:** 1.1.0  
**Base URL:** `https://api.yourdomain.com/api/v1` (or `http://127.0.0.1:8000/api/v1` in local development)  
**Protocol:** HTTPS / WSS  
**Content-Type:** `application/json`

---

## Table of Contents

1. [Role-Based Access Control (RBAC) & Security Architecture](#1-role-based-access-control-rbac--security-architecture)
2. [Authentication & Authorization](#2-authentication--authorization)
   - [POST /auth/login/](#post-authlogin)
   - [POST /auth/token/refresh/](#post-authtokenrefresh)
   - [POST /auth/logout/](#post-authlogout)
   - [POST /auth/change-password/](#post-authchange-password)
   - [GET /auth/me/](#get-authme)
3. [Tenants Management](#3-tenants-management)
   - [GET /tenants/](#get-tenants-superadmin-only)
   - [POST /tenants/](#post-tenants-superadmin-only)
   - [GET /tenants/{id}/](#get-tenantsid)
   - [PATCH /tenants/{id}/](#patch-tenantsid)
4. [Telephony Resources (Extensions & DIDs)](#4-telephony-resources-extensions--dids)
   - [GET /extensions/](#get-extensions)
   - [GET /extensions/{id}/](#get-extensionsid)
   - [GET /dids/](#get-dids)
   - [GET /dids/{id}/](#get-didsid)
5. [Users Management (Unified Provisioning & Updates)](#5-users-management-unified-provisioning--updates)
   - [POST /users/ (Unified User Creation)](#post-users--unified-user-creation)
   - [PATCH /users/{id}/ (Unified User Update)](#patch-usersid--unified-user-update)
   - [GET /users/](#get-users)
   - [GET /users/{id}/](#get-usersid)
   - [DELETE /users/{id}/](#delete-usersid)
   - [POST /users/{id}/reset-password-email/](#post-usersidreset-password-email)
   - [POST /users/{id}/admin-reset-password/](#post-usersidadmin-reset-password)
   - [GET /users/{id}/sip-credentials/](#get-usersidsip-credentials)
6. [Telephony Resource Assignments](#6-telephony-resource-assignments)
   - [Extension Assignment: POST/DELETE /users/{id}/extension/](#61-extension-assignment)
   - [DID Assignment: POST/DELETE /users/{id}/dids/](#62-did-assignment)
   - [FaxBox Assignment: POST/DELETE /users/{id}/fax-boxes/](#63-faxbox-assignment)
   - [VoicemailBox Assignment: POST/DELETE /users/{id}/voicemail-boxes/](#64-voicemailbox-assignment)
7. [Communication APIs (Telephony, Fax, Voicemail, CDR)](#7-communication-apis)
   - [Calls: POST /calls/originate/ & POST /calls/hangup/](#71-calls)
   - [Voicemail: GET /voicemail/messages/ & GET /voicemail/messages/{id}/audio/](#72-voicemail)
   - [Fax: POST /fax/send/ & GET /fax/history/](#73-fax)
   - [CDR: GET /cdr/](#74-cdr)
8. [Inbound FreeSWITCH Webhook Ingestion](#8-inbound-freeswitch-webhook-ingestion)
   - [POST /webhooks/freeswitch/](#post-webhooksfreeswitch)
9. [Audit & Monitoring Logs](#9-audit--monitoring-logs)
   - [GET /audit-logs/](#get-audit-logs)
   - [GET /webhook-logs/](#get-webhook-logs)
10. [Realtime WebSocket Protocol](#10-realtime-websocket-protocol)
11. [Contacts & Directory Management](#11-contacts--directory-management)
   - [GET /contacts/](#get-contacts)
   - [POST /contacts/](#post-contacts)
   - [GET /contacts/{id}/](#get-contactsid)
   - [PUT & PATCH /contacts/{id}/](#put--patch-contactsid)
   - [DELETE /contacts/{id}/](#delete-contactsid)

---

## 1. Role-Based Access Control (RBAC) & Security Architecture

The platform implements a strict 3-tier Role-Based Access Control model:

| Role | Scope | Capabilities | Restrictions |
|---|---|---|---|
| **`superadmin`** | Platform-wide | Manages tenants, views all audit logs, manages resources across all tenants. | **Must provide `tenant_id`** (`?tenant_id=...` or `X-Tenant-ID` header) when listing scoped resources (`/extensions/`, `/dids/`). |
| **`admin`** | Single Tenant | Manages users, extension/DID assignments, fax boxes, voicemail boxes within own tenant. | **Blocked from `GET/POST /tenants/`** (`403 Forbidden`). Can only view/edit own tenant via `/tenants/{id}/`. |
| **`user`** | Personal | Receives calls, sends/receives faxes, listens to voicemails, registers softphone via SIP credentials. | **Blocked from all administrative endpoints** (`/extensions/`, `/dids/`, `/users/`, `/tenants/`, logs). |

### Security Invariants
- **Secret Encryption at Rest**: SIP passwords and FreeSWITCH API keys are stored encrypted via Fernet symmetric encryption (`SecretService`). Decrypted secrets exist strictly in-memory and are NEVER persisted to logs or sent via WebSockets.
- **Secret Sanitization**: Inbound webhooks automatically redact `password`, `sip_password`, `api_key`, `secret`, and `token` fields before writing to `WebhookLog`.
- **48-Hour Webhook Log TTL**: Webhook logs expire and are pruned after 48 hours via indexed `expires_at`.
- **Resource Routing Pattern**: Voicemail and Fax messages are NOT stored locally in PostgreSQL. The backend holds only resource assignments (`User.voicemail_boxes`, `User.fax_boxes`) and routes inbound FreeSWITCH events to assigned users.

### Standard Pagination & Export Parameters
All listing endpoints (`/tenants/`, `/users/`, `/extensions/`, `/dids/`, `/contacts/`, `/audit-logs/`, `/webhook-logs/`, `/cdr/`, `/fax/boxes/`, `/fax/files/`, `/recordings/`, `/voicemail/messages/`) support standard pagination and bulk export query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | 1-based page index. |
| `page_size` | integer | `25` | Number of results per page (up to `1000`). |
| `export_all` | boolean / string | `false` | When set to `true` (or `?export=all`), bypasses pagination and returns all matching records in a single array. |

---

## 2. Authentication & Authorization

All protected endpoints require an `Authorization` header:
```http
Authorization: Bearer <access_token>
```

### POST `/auth/login/`
Authenticates a user with email and application password. Returns JWT access/refresh tokens and user profile.

#### Request Body
```json
{
  "email": "root@tcx.com",
  "password": "YourSecurePassword123"
}
```

#### Response `200 OK`
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": "dc151ca8-2c62-4f48-a3f0-cbb58c2e8aea",
    "email": "agent@tcx.com",
    "first_name": "Agent",
    "last_name": "Smith",
    "role": "user",
    "is_active": true,
    "is_first_login": true,
    "must_change_password": true,
    "tenant": {
      "id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
      "freeswitch_tenant_uuid": "7fae0a2e-4b21-4322-81fa-223456789abc",
      "tenant_code": "TCX",
      "tenant_name": "TCX Communications",
      "sip_domain": "sip.example.com"
    },
    "features": {
      "calling": true,
      "messaging": true,
      "fax": false
    },
    "extension": {
      "id": "3163c924-e0fd-458e-8a05-912889f428f6",
      "extension_number": "101",
      "sip_username": "101-TCX",
      "sip_password": "PlaintextSipPassword",
      "transport_type": "TLS"
    },
    "dids": [],
    "fax_boxes": [],
    "voicemail_boxes": [],
    "created_at": "2026-08-28T20:12:27.756953Z"
  }
}
```

#### Authentication & Feature Safeguards (`400 Bad Request`)
- **If Calling is Enabled**: Users must have both an extension and a DID assigned:
  - **No Extension & No DID**: `{"detail": "Calling is enabled, but you do not have an extension or a DID assigned. Please contact your administrator."}`
  - **No Extension**: `{"detail": "Calling is enabled, but you do not have an extension assigned. Please contact your administrator."}`
  - **No DID**: `{"detail": "Calling is enabled, but you do not have a DID assigned. Please contact your administrator."}`
- **If Messaging is Enabled & Calling is Disabled**: Users require an assigned DID:
  - **No DID Assigned**: `{"detail": "Messaging is enabled, but you do not have a DID assigned. Please contact your administrator."}`

#### Resource Assignment Rules:
- **Extensions**: Only allowed when `calling` is enabled for the tenant.
- **DIDs**: Allowed when `calling` OR `messaging` is enabled. Blocked if both are disabled.
- **FaxBoxes**: Only allowed when `fax` is enabled for the tenant.

### POST `/auth/token/refresh/`
Refreshes an expired access token using a valid refresh token.

#### Request Body
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### POST `/auth/logout/`
Logs out the user and invalidates their session by blacklisting the refresh token.

#### Request Body
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

#### Response `200 OK`
```json
{
  "detail": "Successfully logged out."
}
```

### POST `/auth/change-password/`
Allows authenticated users to change their own password (e.g. following first-time login or an admin temporary password reset). Upon success, `must_change_password` is reset to `false`.

#### Headers
`Authorization: Bearer <access_token>`

#### Request Body
```json
{
  "current_password": "TemporaryPassword123!",
  "new_password": "MyNewSecurePassword123!"
}
```

#### Response `200 OK`
```json
{
  "status": "success",
  "detail": "Password changed successfully.",
  "must_change_password": false
}
```

### GET `/auth/me/`
Returns the currently authenticated user's profile and telephony configurations.

---

## 3. Tenants Management

### GET `/tenants/` *(Superadmin Only)*
Lists all tenants with live telephony resource counts. Blocked for `admin` and `user` roles (`403 Forbidden`).

#### Query Parameters
- `is_active` (boolean, optional): Filter by `true` or `false`.
- `search` (string, optional): Case-insensitive search on `tenant_code` or `tenant_name`.

#### Response `200 OK`
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
      "freeswitch_tenant_uuid": "7fae0a2e-4b21-4322-81fa-223456789abc",
      "tenant_code": "TCX",
      "tenant_name": "TCX Communications",
      "features": {
        "calling": true,
        "messaging": true,
        "fax": true
      },
      "is_active": true,
      "extensions_count": 12,
      "dids_count": 4,
      "users_count": 8,
      "created_at": "2026-08-29T11:40:15.437672Z",
      "updated_at": "2026-08-29T11:40:15.437685Z"
    }
  ]
}
```

> **Note on Voicemail**: `voicemail` is no longer a separate tenant feature flag. If `calling` is enabled for the tenant, voicemail capability is automatically enabled.

### POST `/tenants/` *(Superadmin Only)*
Creates a new tenant.

### GET `/tenants/{id}/` *(Superadmin & Tenant Admin)*
Retrieves tenant details. Tenant admins can only access their own tenant ID.

### PATCH `/tenants/{id}/` *(Superadmin & Tenant Admin)*
Updates tenant features or details.

---

## 4. Telephony Resources (Extensions & DIDs)

### GET `/extensions/`
Lists extensions for softphone assignment.
- **For `superadmin`**: `tenant_id` query parameter (or `X-Tenant-ID` header) is **strictly required**. Accepts Tenant UUID, FreeSWITCH UUID, or Tenant Code (e.g. `?tenant_id=TCX`).
- **For `admin`**: Automatically scoped to the administrator's tenant.

#### Query Parameters
- `tenant_id` *(required for superadmin)*: Internal UUID, FreeSWITCH UUID, or Tenant Code.
- `is_assigned` *(boolean, optional)*: `true` to filter assigned, `false` to filter unassigned pool.
- `search` *(string, optional)*: Search by extension number or SIP username.

#### Response `200 OK`
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "3163c924-e0fd-458e-8a05-912889f428f6",
      "tenant_id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
      "tenant_code": "TCX",
      "tenant_name": "TCX Communications",
      "freeswitch_object_id": "fs-ext-101-uuid",
      "extension_number": "101",
      "sip_username": "101-TCX",
      "transport_type": "TLS",
      "assigned_user_id": "18f2f458-bf87-43c3-888a-2115f5d8e785",
      "assigned_user_email": "user@example.com",
      "created_at": "2026-08-29T11:44:54.671641Z",
      "updated_at": "2026-08-29T11:51:04.640230Z"
    }
  ]
}
```

### GET `/extensions/{id}/`
Retrieves single extension details.

### PATCH `/extensions/{id}/transport/` *(Superadmin Only)*
Updates the SIP transport type for an extension.

#### Permissions
- Restricted strictly to `superadmin`. Tenant admins and regular users receive `403 Forbidden`.

#### Request Body
```json
{
  "transport_type": "TLS"
}
```
*Supported `transport_type` choices*: `UDP`, `TCP`, `TLS`, `DTLS` (case-insensitive).

#### Response `200 OK`
```json
{
  "id": "3163c924-e0fd-458e-8a05-912889f428f6",
  "extension_number": "101",
  "sip_username": "101-TCX",
  "transport_type": "TLS",
  "updated_at": "2026-09-02T17:07:40.353337Z"
}
```

> **Alternative Endpoints**:
> - `PATCH /api/v1/extensions/{id}/` with `{"transport_type": "TLS"}` *(Superadmin Only)*
> - `PATCH /api/v1/users/{id}/extension/transport/` with `{"transport_type": "TLS"}` *(Superadmin Only)*

---

### GET `/dids/`
Lists phone numbers (DIDs) with tenant scoping, capabilities, and assigned users.
- **For `superadmin`**: `tenant_id` is strictly required (`?tenant_id=TCX` or header `X-Tenant-ID: TCX`).
- **For `admin`**: Automatically scoped to own tenant.

#### Query Parameters
- `tenant_id` *(required for superadmin)*: Internal UUID, FreeSWITCH UUID, or Tenant Code.
- `search` *(string, optional)*: Search by phone number (e.g. `+1832`).

#### Response `200 OK`
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "38a784eb-e08a-441f-9e01-893b15728163",
      "tenant_id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
      "tenant_code": "TCX",
      "tenant_name": "TCX Communications",
      "freeswitch_object_id": "fs-did-8321234567-uuid",
      "number": "+18321234567",
      "name": "Main Line",
      "did_number": "+18321234567",
      "did_name": "Main Line",
      "assigned_users_count": 1,
      "assigned_users": [
        {
          "id": "18f2f458-bf87-43c3-888a-2115f5d8e785",
          "email": "user@example.com"
        }
      ],
      "created_at": "2026-08-29T11:45:02.156591Z",
      "updated_at": "2026-08-29T11:45:02.156602Z"
    }
  ]
}
```

### GET `/dids/{id}/`
Retrieves single DID details.

---

## 5. Users Management (Unified Provisioning & Updates)

*(Restricted to `superadmin` and `admin` roles)*

The User API provides **unified, atomic endpoints**: you can create or update a user profile and simultaneously assign/unassign their **Extension**, **DIDs**, **FaxBoxes**, and **VoicemailBoxes** in a **single API call**.

---

### POST `/users/` — Unified User Creation
Creates a user and atomically provisions all telephony resources in a single transaction.

#### Request Body (Passing Pre-existing Resource IDs)
Since Extensions and DIDs are already provisioned into the database via FreeSWITCH webhooks (`extension.created`, `did.created`), clients can directly pass their UUID `id`s:

```json
{
  "email": "agent1@tcx.com",
  "password": "SecurePassword123!",
  "first_name": "Agent",
  "last_name": "One",
  "role": "user",
  "tenant_id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
  "extension_id": "3163c924-e0fd-458e-8a05-912889f428f6",
  "did_ids": [
    "38a784eb-e08a-441f-9e01-893b15728163"
  ],
  "fax_boxes": [
    {
      "fax_uuid": "978c7337-d642-4cd7-a38a-d0a61c2cfbde",
      "fax_caller_id_name": "Sales Fax",
      "fax_caller_id_number": "+18325550123"
    }
  ],
  "voicemail_boxes": [101, 1001]
}
```

> **Flexible Resource Resolution & Automatic Tenant Inference**:
> - `extension_id`: Accepts the database UUID `id` (e.g. `3163c924-e0fd-...`), FreeSWITCH object UUID, or extension number (e.g. `"101"`).
> - `did_ids`: Accepts a list of DID database UUID `id`s, FreeSWITCH object UUIDs, or E.164 phone numbers (e.g. `["+18321234567"]`).
> - `tenant_id`: **Optional if `extension_id` is supplied!** When creating a user with a pre-existing extension, the backend automatically infers the tenant from the extension itself.

#### Response `201 Created`
```json
{
  "id": "378289bb-2ad2-479e-b466-2ec2ae79651f",
  "email": "agent1@tcx.com",
  "first_name": "Agent",
  "last_name": "One",
  "role": "user",
  "is_active": true,
  "tenant": {
    "id": "faa447b0-f40c-4bcf-b651-4131f6634f27",
    "freeswitch_tenant_uuid": "7fae0a2e-4b21-4322-81fa-223456789abc",
    "tenant_code": "TCX",
    "tenant_name": "TCX Communications",
    "sip_domain": "sip.example.com"
  },
  "features": { ... },
  "extension": {
    "id": "3163c924-e0fd-458e-8a05-912889f428f6",
    "extension_number": "101",
    "sip_username": "101-TCX",
    "transport_type": "TLS"
  },
  "dids": [
    {
      "id": "38a784eb-e08a-441f-9e01-893b15728163",
      "number": "+18321234567",
      "name": "Main Line"
    }
  ],
  "fax_boxes": [
    {
      "fax_uuid": "978c7337-d642-4cd7-a38a-d0a61c2cfbde",
      "fax_caller_id_name": "Sales Fax",
      "fax_caller_id_number": "+18325550123"
    }
  ],
  "voicemail_boxes": [101, 1001],
  "created_at": "2026-08-29T12:20:00.000000Z"
}
```

---

### PATCH `/users/{id}/` — Unified User Update
Atomically updates user attributes and/or updates/replaces resource assignments. Any field omitted remains unchanged.

#### Request Body
```json
{
  "first_name": "Agent",
  "last_name": "Updated",
  "role": "admin",
  "sip_domain": "custom.sip.example.com",
  "extension_id": null,
  "did_ids": ["+18321234567"],
  "voicemail_boxes": [101, 2002]
}
```

> **Unassigning Resources**:
> - Pass `"extension_id": null` to unassign the current extension.
> - Pass `"did_ids": []` to remove all assigned DIDs, or provide a new list to synchronize.

#### Response `200 OK`
Returns the updated user profile with all nested resources.

---

### GET `/users/`
Lists users. Superadmins can filter by `?tenant_id=...`; tenant admins are scoped to their own tenant.

### GET `/users/{id}/`
Retrieves full user profile with all nested resources (`tenant`, `extension`, `dids`, `fax_boxes`, `voicemail_boxes`).

### DELETE `/users/{id}/`
Deletes user and unlinks all assigned resources.

### POST `/users/{id}/reset-password-email/`
*(Admin or Superadmin)*  
Generates a cryptographically secure 12-character temporary password, sets the user's password to it, marks `must_change_password = true`, and dispatches an email to the user with their temporary password and login instructions.

#### Headers
`Authorization: Bearer <access_token>`

#### Response `200 OK`
```json
{
  "status": "success",
  "detail": "Temporary password sent to user@example.com."
}
```

### POST `/users/{id}/admin-reset-password/`
*(Admin or Superadmin)*  
Allows an administrator to manually override and set a user's password. The request body includes an option (`must_change_password`) specifying whether the user must change their password upon their next login.

#### Headers
`Authorization: Bearer <access_token>`

#### Request Body
```json
{
  "new_password": "AdminAssignedPassword123!",
  "must_change_password": true
}
```

#### Response `200 OK`
```json
{
  "status": "success",
  "detail": "Password updated successfully.",
  "must_change_password": true
}
```

### GET `/users/{id}/sip-credentials/`
Decrypts in-memory and returns SIP credentials for softphone client registration.  
*Authorized for: the user themselves, their tenant administrator, or a superadmin.*

#### Response `200 OK`
```json
{
  "extension_number": "101",
  "sip_username": "101-TCX",
  "sip_password": "PlaintextSipPasswordDecryptedInMemory",
  "sip_domain": "sip.example.com",
  "transport_type": "TLS"
}
```

---

## 6. Telephony Resource Assignments

### 6.1 Extension Assignment
- **Assign:** `POST /users/{id}/extension/` with `{"extension_id": "<uuid>"}`
- **Unassign:** `DELETE /users/{id}/extension/`

### 6.2 DID Assignment
- **Grant Access:** `POST /users/{id}/dids/` with `{"did_id": "<uuid>"}`
- **Revoke Access:** `DELETE /users/{id}/dids/{did_id}/`

### 6.3 FaxBox Assignment
- **Assign FaxBox:** `POST /users/{id}/fax-boxes/`
  ```json
  {
    "fax_uuid": "978c7337-d642-4cd7-a38a-d0a61c2cfbde",
    "fax_caller_id_name": "Sales Fax",
    "fax_caller_id_number": "+18325550123"
  }
  ```
- **Remove FaxBox:** `DELETE /users/{id}/fax-boxes/{fax_uuid}/`

### 6.4 VoicemailBox Assignment
- **Assign VoicemailBox:** `POST /users/{id}/voicemail-boxes/`
  ```json
  {
    "voicemail_box_id": 1001
  }
  ```
- **Remove VoicemailBox:** `DELETE /users/{id}/voicemail-boxes/{box_id}/`

---

## 7. Communication APIs (FreeSWITCH Client API Gateway / Proxy)

The backend acts as an authenticated proxy for FreeSWITCH / Cloud PBX Client APIs.
- Clients call the backend with their standard JWT token (`Authorization: Bearer <jwt>`).
- The backend automatically decrypts the tenant's API key in-memory, checks feature flags (`calling`, `voicemail`, `fax`), and injects `Authorization: ApiKey <key>`.
- Regular users are automatically scoped to their assigned `voicemail_boxes`, `fax_boxes`, and `extension`.
- Binary media (voicemail audio WAV, fax document PDF) is streamed chunk-by-chunk directly through to the client without buffering in server RAM.

---

### 7.1 Voicemail Proxy

| Endpoint | Method | Description |
|---|---|---|
| `/voicemail/messages/` | `GET` | If no `voicemail_id`: returns mailbox summaries. If `voicemail_id`: returns paginated messages. Regular users are auto-scoped to `User.voicemail_boxes`. |
| `/voicemail/messages/{message_uuid}/` | `GET` | Single voicemail message details. |
| `/voicemail/messages/{message_uuid}/audio/` | `GET` | Streams voicemail audio binary (`audio/wav`) chunk-by-chunk directly to client. |
| `/voicemail/messages/{message_uuid}/mark-read/` | `PATCH` | Updates read status (`{"read": true}`). |
| `/voicemail/messages/{message_uuid}/` | `DELETE` | Permanently deletes voicemail message and audio file. |
| `/voicemail/unread-counts/` | `GET` | Returns unread voicemail count grouped by mailbox ID. |

---

### 7.2 Fax Proxy

| Endpoint | Method | Description |
|---|---|---|
| `/fax/boxes/` | `GET` | Lists fax boxes for tenant (scoped to assigned `User.fax_boxes` for regular users). |
| `/fax/boxes/{fax_uuid}/` | `GET` | Single fax box detail. |
| `/fax/files/` | `GET` | Lists inbound and outbound fax transmissions (`status=received\|sent\|pending\|failed`, `direction=inbound\|outbound`, `search`). |
| `/fax/files/{fax_file_uuid}/` | `GET` | Single fax transmission detail. |
| `/fax/files/{fax_file_uuid}/download/` | `GET` | Streams fax document directly as PDF (`application/pdf`). Optional `?attachment=true`. |
| `/fax/send/` | `POST` | Queues outbound fax transmission. Multipart form-data: `fax_uuid`, `destination_number`, `file` (PDF). |
| `/fax/files/{fax_file_uuid}/cancel/` | `POST` | Cancels active or queued outbound fax. |
| `/fax/files/{fax_file_uuid}/` | `DELETE` | Deletes fax transmission record and PDF file. |

---

### 7.3 CDR & Call Analytics Proxy

| Endpoint | Method | Description |
|---|---|---|
| `/cdr/` | `GET` | Queries call records. Supports `direction`, `start`, `end`, `hangup_cause`, `missed_call`, `status`, `search`, `number`, `extension`, `export=csv\|json`, `page`, `page_size`. |
| `/cdr/{xml_cdr_uuid}/` | `GET` | Single call record details. |
| `/cdr/summary/` | `GET` | Aggregate call statistics (answer rate, duration, inbound vs outbound breakdown). |
| `/cdr/hourly-stats/` | `GET` | 24 hourly buckets in local timezone (`date`, `utc_offset`, `extension`). |
| `/cdr/daily-summary/` | `GET` | Day-by-day inbound/outbound metrics over date window (`start`, `end`). |
| `/cdr/top-extensions/` | `GET` | Top 10 extensions ranked by call volume (`start`, `end`). |
| `/cdr/extension-call-summary/` | `GET` | Detailed inbound/outbound breakdown for a specific extension (`extension`, `start`, `end`). |
| `/cdr/active-extensions/` | `GET` | Extensions with recorded activity in timeframe (`start`, `end`). |

---

### 7.4 Call Recordings Proxy

| Endpoint | Method | Description |
|---|---|---|
| `/recordings/` | `GET` | Lists recorded calls with search and datetime filters. |
| `/recordings/{recording_uuid}/` | `GET` | Metadata for a specific call recording. |
| `/recordings/{recording_uuid}/audio/` | `GET` | Streams call audio recording (`audio/wav`) directly to the client. |
| `/recordings/{recording_uuid}/` | `DELETE` | Deletes recording entry and on-disk audio file. |

---

## 8. Inbound FreeSWITCH Webhook Ingestion

### POST `/webhooks/freeswitch/`
Receives FreeSWITCH notifications and synchronizes database state.

#### Supported Events & Behaviors
1. **`tenant.created` & `api_key.created`**:
   - Accepts `tenant_uuid` or `tenant_id`, `tenant_code`, `tenant_name`, `api_key`, `sip_domain`.
   - In-memory encryption via `SecretService.encrypt()`.
   - Auto-provisions or updates `Tenant` with `encrypted_api_key`.
2. **`extension.created` & `extension.updated`**:
   - Native field mapping: accepts `phone` for `extension_number` and `password` for `sip_password`.
   - Partial update safe: preserves existing fields when only object notifications are received.
   - Encrypts SIP password in-memory.
   - Auto-provisions parent tenant if not already present.
3. **`extension.deleted`**:
   - Unlinks from assigned user (`on_delete=SET_NULL`) and deletes local extension.
4. **`did.created` & `did.updated`**:
   - Supports `did_number` / `number` and `did_name` / `name`.
   - Synchronizes DID number and name.
5. **`did.deleted`**:
   - Cleans up DID and associated `UserDID` assignments.
6. **`voicemail.received` & `fax.received`**:
   - Routes inbound communication events to users based on PostgreSQL JSONB containment (`@>`) over `User.voicemail_boxes` and `User.fax_boxes`.
7. **Secret Sanitization**:
   - All `password`, `sip_password`, and `api_key` values are replaced with `[REDACTED]` before writing to `WebhookLog`.
8. **Retention**:
   - `WebhookLog` records auto-expire after 48 hours.

---

## 9. Audit & Monitoring Logs

- **GET `/audit-logs/`**: Permanent append-only security trail. Scoped to tenant for admins; global for superadmins.
- **GET `/webhook-logs/`**: 48-hour temporary troubleshooting logs for carrier/FreeSWITCH webhooks.

---

## 10. Realtime WebSocket Protocol

**Endpoint:** `wss://api.yourdomain.com/ws/realtime/?token=<JWT_ACCESS_TOKEN>`

When connected, clients receive real-time call states, fax events, and voicemail notifications routed through Django Channels and the PostgreSQL Outbox pattern.

---

## 11. Contacts & Directory Management

The Contacts module provides unified management of contacts for both **Company Directory** (tenant-wide, shared phonebook) and **User-Based Directory** (personal, user-private contacts).

### Directory Types & Permission Matrix

| Directory Type | View / Search | Create / Update / Delete | Owner Field | Description |
|---|---|---|---|---|
| `personal` | Contact Owner (and SuperAdmin) | Contact Owner (and SuperAdmin) | `User.id` | User's private address book. Other tenant users cannot see or modify. |
| `company` | All authenticated users in the tenant | Tenant `admin` & `superadmin` | `null` | Organization-wide shared directory (e.g. general numbers, queues, vendors, clients). |

---

### GET `/contacts/`
Lists contacts accessible to the authenticated user (Company Directory contacts in their tenant + their own Personal Directory contacts).

#### Query Parameters
- `directory_type`: Filter by `company` or `personal`.
- `search`: Case-insensitive search across `first_name`, `last_name`, `email`, `notes`, `numbers__number`, and `numbers__label`.
- `is_favorite`: Filter by `true` or `false`.
- `page`: Page number (default: 1).
- `page_size`: Results per page (default: 25).
- `tenant_id`: *(SuperAdmin only)* Target specific tenant.

#### Response `200 OK`
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "c13ef8eb-be58-4f21-ac67-c9c198837166",
      "tenant": "3cf3ce14-2662-4be2-b9e3-2c661f3379c6",
      "owner": "81f6f1e6-52ab-4dc0-b84b-62e2abc24995",
      "owner_email": "alice@acme.com",
      "created_by": "81f6f1e6-52ab-4dc0-b84b-62e2abc24995",
      "created_by_email": "alice@acme.com",
      "directory_type": "personal",
      "first_name": "Sarah",
      "last_name": "Connor",
      "full_name": "Sarah Connor",
      "email": "sarah@example.com",
      "notes": "VIP Client from Sector 4",
      "is_favorite": true,
      "numbers": [
        {
          "id": "1e72e1fa-58cb-4654-a690-349079a099a4",
          "number": "+12025550111",
          "label": "Work",
          "is_primary": true,
          "created_at": "2026-09-03T20:45:00Z",
          "updated_at": "2026-09-03T20:45:00Z"
        },
        {
          "id": "4b68e0de-75bf-4091-a1b9-1e149cb2e09c",
          "number": "+12025550122",
          "label": "Mobile",
          "is_primary": false,
          "created_at": "2026-09-03T20:45:00Z",
          "updated_at": "2026-09-03T20:45:00Z"
        }
      ],
      "created_at": "2026-09-03T20:45:00Z",
      "updated_at": "2026-09-03T20:45:00Z"
    }
  ]
}
```

---

### POST `/contacts/`
Creates a new contact.

#### Validation Rules
- `first_name`: **Required** string (cannot be blank).
- `numbers`: **Required** array containing at least one valid phone number object `{"number": "...", "label": "..."}`.
- `label`: Optional string. Defaults to `"Mobile"`. Custom labels are fully supported (e.g. `"Work"`, `"Mobile"`, `"Direct Desk"`, `"Emergency Line"`).
- Multiple numbers can have identical labels or different labels.
- `directory_type`: Defaults to `"personal"`. If `"company"` is specified, requester must have `admin` or `superadmin` role.

#### Request Body
```json
{
  "directory_type": "personal",
  "first_name": "Sarah",
  "last_name": "Connor",
  "email": "sarah@example.com",
  "notes": "VIP Client from Sector 4",
  "is_favorite": true,
  "numbers": [
    {
      "number": "+12025550111",
      "label": "Work",
      "is_primary": true
    },
    {
      "number": "+12025550122",
      "label": "Mobile"
    },
    {
      "number": "1004",
      "label": "Direct Desk"
    }
  ]
}
```

#### Response `201 Created`
Returns the complete created contact object with generated UUIDs and numbers.

---

### GET `/contacts/{id}/`
Retrieves a contact and all its phone numbers by UUID. Returns `404 Not Found` if the contact does not belong to the user's scope or tenant.

---

### PUT & PATCH `/contacts/{id}/`
Updates contact fields and associated phone numbers.
- **`PATCH`**: Partial update. Send only the fields to modify (e.g. `{"first_name": "Sara", "is_favorite": true}`).
- **`PUT`**: Replaces contact details. If `numbers` is provided, replaces the contact's phone numbers. Must include at least one number.

---

### DELETE `/contacts/{id}/`
Deletes the contact and cascades the deletion to all associated phone numbers.
- Response: `200 OK` with `{"detail": "Contact deleted successfully."}`.

