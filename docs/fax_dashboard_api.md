# Fax Dashboard API

Two layers:

- **Unified backend** (`/api/v1/fax/...`) — what the frontend calls. JWT auth (`Authorization: Bearer <token>`). Requires the tenant `fax` feature. Regular users (`role=user`) are limited to their assigned `fax_boxes`.
- **PBX / tcxconnect Client API** (`/api/v1/client/<tenant_uuid>/fax/...`) — called only by the Unified backend. Auth: `Authorization: ApiKey <tenant key>`.

Read/unread lives in Unified (`FaxTag.read_at`), keyed by `fax_file_uuid`. The PBX has no read concept.

**Definitions:** inbound = fax status `received`; outbound = every other status; ongoing = status `pending`.

---

## Dropdown flow: "All" or one fax box

1. `GET /api/v1/fax/boxes/` → populate the dropdown (first option "All").
2. "All" → call endpoints with no `fax` param (or `fax=all`).
3. A box selected → call the same endpoints with `?fax=<fax_uuid>`.

`fax` accepts one UUID or a comma-separated list. Regular users selecting a box they don't hold get `403`; with "All" they are automatically limited to their own boxes. A malformed UUID returns `400`.

---

## Unified endpoints

### 1. List fax boxes (dropdown)
`GET /api/v1/fax/boxes/`

```json
[
  {
    "fax_uuid": "1b9f3c1e-6a4d-4c55-9b0e-2f6d8a7c1111",
    "fax_name": "Main Fax",
    "fax_extension": "9000",
    "fax_email": "fax@example.com",
    "fax_caller_id_name": "Acme",
    "fax_caller_id_number": "+15551230000",
    "fax_enabled": true
  }
]
```

### 2. Single fax box
`GET /api/v1/fax/boxes/{fax_uuid}/` → one object with the same fields. `404` if not found.

### 3. Dashboard totals
`GET /api/v1/fax/dashboard/?fax=<fax_uuid|all>` (`fax` optional)

```json
{
  "inbound_total": 120,
  "outbound_total": 45,
  "ongoing": 2,
  "failed": 6,
  "inbound_read": 90,
  "inbound_unread": 30,
  "inbound_assigned": 70,
  "inbound_unassigned": 50,
  "inbound_pending": 60,
  "inbound_in_progress": 35,
  "inbound_completed": 25
}
```

All `inbound_*` splits are over received faxes in scope, so each pair/trio sums to `inbound_total`:
`read + unread`, `assigned + unassigned`, `pending + in_progress + completed`.
`inbound_pending` includes faxes with no workflow record yet (default state). The workflow state here is the local review status, not the PBX transmission status. Errors: `400` fax feature disabled or bad UUID, `403` box not permitted, `502/504` PBX unreachable.
A regular user with no assigned boxes gets `200` with `{"count":0,"results":[],"summary":{"total":0},"detail":"No fax boxes are assigned to your account."}`.

### 4. Fax list (read/unread, ongoing, inbound)
`GET /api/v1/fax/files/`

| Param | Values |
|---|---|
| `fax` | box UUID(s) or `all` |
| `direction` | `inbound` / `outbound` |
| `status` | `received` / `sent` / `pending` / `failed` |
| `search` | caller id / number / filename / box name |
| `assigned_to` | `me` |
| `page`, `page_size` | pagination (default 20) |

Dashboard tabs map to:
- **Ongoing:** `?status=pending`
- **Inbound:** `?direction=inbound`
- **Unread / read:** filter the returned rows on `is_read` (there is no server-side unread filter yet).

```json
{
  "count": 120,
  "next": "…?page=2",
  "previous": null,
  "results": [
    {
      "fax_file_uuid": "7d2a…",
      "fax": "1b9f3c1e-6a4d-4c55-9b0e-2f6d8a7c1111",
      "fax_box_name": "Main Fax",
      "fax_file_status": "received",
      "direction": "inbound",
      "fax_file_pages": 3,
      "fax_file_name": "fax_123.pdf",
      "fax_caller_id_name": "John",
      "fax_caller_id_number": "+15557654321",
      "fax_destination_number": "+15551230000",
      "fax_station_id": "+15557654321",
      "retry_count": 0,
      "insert_date": "2026-11-16T10:21:00Z",
      "file_size_bytes": 48211,
      "download_url": "…",
      "status": "pending",
      "assigned_to": null,
      "is_read": false,
      "read_at": null,
      "read_by": null
    }
  ],
  "summary": {"total": 120, "pending": 2, "sent": 40, "received": 114, "failed": 6}
}
```

`fax_file_status` is the transmission state from the PBX. `status` is the local review workflow (`pending` / `in_progress` / `completed`) — don't confuse the two.
`read_by` when read: `{"user_id": "…", "name": "Jane Doe", "extension_number": "101"}`.

### 5. Single fax (file) information
`GET /api/v1/fax/files/{fax_file_uuid}/` → one object with the same fields as a list row (including `is_read`, `read_at`, `read_by`, `assigned_to`). `404` if not found.

### 6. Mark read / unread
`POST /api/v1/fax/files/{fax_file_uuid}/mark-read/`
`POST /api/v1/fax/files/{fax_file_uuid}/mark-unread/`
No request body. Returns the single-fax object above with updated read fields.

- `mark-read` keeps the first reader if already read.
- `mark-unread` clears `read_at` and `read_by`.
- Downloading (`GET …/download/`) also marks a fax read automatically.
- `403` if a regular user doesn't hold the fax's box; `404` if the fax doesn't exist.

Read state is shared: one person marking a fax read marks it read for everyone.

---

## PBX endpoints (called by Unified only)

Base: `/api/v1/client/<tenant_uuid>/fax/` — header `Authorization: ApiKey <key>`; `tenant_uuid` must match the key's tenant (else `403`).

### `GET …/fax/counts/?fax=<uuid[,uuid]|all>`
```json
{"inbound_total": 120, "outbound_total": 45, "ongoing": 2, "failed": 6}
```

### `GET …/fax/received-uuids/?fax=<uuid[,uuid]|all>`
Lightweight list used to count read/unread (one query, single column).
```json
{"count": 120, "uuids": ["7d2a…", "9c41…"]}
```

Existing endpoints used by the flow: `GET …/fax/` (boxes), `GET …/fax/<fax_uuid>/` (one box), `GET …/fax/files/` (list), `GET …/fax/files/<fax_file_uuid>/` (one fax).

Both new endpoints return `400` for a malformed `fax` UUID.

---

## How the dashboard computes read/unread
1. `counts/` → totals.
2. `received-uuids/` → all inbound UUIDs in scope.
3. Unified aggregates `FaxTag` rows for those UUIDs: `read_at` set → read, `assigned_to` set → assigned, `status` in_progress / completed.
4. Unread, unassigned and pending are the remainder (`len(uuids)` minus the tagged count), because untouched faxes have no `FaxTag` row.

Faxes never opened have no `FaxTag` row and count as unread.

## Deployment notes
- Restart tcxconnect to load the two new PBX routes; the Unified dashboard returns a PBX `404` until then.
- No database migrations are needed in either project.
