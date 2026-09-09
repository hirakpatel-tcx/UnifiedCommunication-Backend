# Telnyx SMS/MMS Messaging — Implementation Plan

## Decisions

- **Integration approach:** Telnyx REST API directly via `httpx`, following the existing `FreeSwitchClientService` pattern. No Telnyx SDK — messaging is a thin surface (send + webhooks) that doesn't justify an extra dependency and its own auth/version lifecycle.
- **Credentials:** Single platform-wide Telnyx API key (`TELNYX_API_KEY`), stored in settings/env like other platform credentials — not per-tenant like FreeSWITCH. Tenants are distinguished by a **Telnyx Messaging Profile ID** stored on `Tenant`.
- **Conversation model:** Messaging supports both 1:1 and **group threads**. Groups are true shared conversations — carrier-level group MMS via Telnyx, where any participant's reply is visible to everyone in the thread (not a broadcast-with-private-replies model).
- **RCS:** Deferred to phase 2. The `Conversation` / `Message` model below is designed so RCS can be added later (a `channel` field + `rich_content` JSON field on `Message`) without reworking the schema. RCS also requires a Telnyx/Google Agent registration and brand approval — an operational step with its own lead time, tracked separately from this build.
- **Contact enrichment:** Reuses the existing `apps/contacts/services.py` lookup (built for the CDR/voicemail `contact_saved`/`contact_id` annotation) to resolve conversation participants against saved Contacts.

## Why persist messages locally

Unlike CDR and voicemail — which stay in FreeSWITCH/Cloud PBX and are only proxied live (see `apps/common/cdr_views.py`, `apps/voicemail/views.py`) — Telnyx has no queryable message-history API. This app's database is the system of record for message threads.

## Data model — new `apps/messaging` app

### `Conversation`
| field | type | notes |
|---|---|---|
| `tenant` | FK → Tenant | |
| `did` | FK → DID | the tenant-owned number this thread is anchored to |
| `is_group` | bool | true when participant count > 1 |
| `subject` | CharField, optional | group thread label |
| `last_message_at` | datetime | for inbox sorting |

### `ConversationParticipant`
| field | type | notes |
|---|---|---|
| `conversation` | FK → Conversation | |
| `phone_number` | CharField (E.164) | external participant |
| `contact` | FK → Contact, nullable | resolved via `apps/contacts/services.py` |

Unique together: `(conversation, phone_number)`.

### `Message`
| field | type | notes |
|---|---|---|
| `conversation` | FK → Conversation | |
| `tenant`, `did` | FK | denormalized for query convenience |
| `user` | FK → User, nullable | sender; null for inbound |
| `direction` | `inbound` / `outbound` | |
| `from_number` | CharField | the actual sending leg |
| `body` | TextField | |
| `media_urls` | JSONField (list) | MMS attachments |
| `telnyx_message_id` | CharField, unique | one send = one message ID, including group MMS |
| `status` | queued / sent / delivered / failed / received | |
| `error_code` / `error_detail` | nullable | |
| `sent_at` / `delivered_at` | nullable datetime | |

*(Phase 2 / RCS: add `channel` (`sms`/`mms`/`rcs`) and `rich_content` JSON for cards/carousels/quick-replies.)*

## Conversation resolution logic

- **Inbound:** Telnyx's `message.received` payload includes the full participant set (`from` + all `to` numbers) for group MMS. Find-or-create a `Conversation` by matching the exact normalized participant set + DID, scoped to tenant. Create/match `ConversationParticipant` rows, resolving each against Contacts.
- **Outbound:** Client sends `did` + `to_numbers: [...]` (1 or many). Resolve/create the `Conversation` the same way — by exact participant set — so repeat sends to the same group land in the same thread.

## Send flow

`POST /api/v1/messaging/messages/send/`
- Validates `messaging` tenant feature flag and DID ownership.
- Resolves/creates `Conversation` + `ConversationParticipant`s.
- One Telnyx API call (`POST /v2/messages`) with all recipient numbers — Telnyx handles the group MMS fan-out natively.
- One `Message` row created for the send; updated with `telnyx_message_id` and status from Telnyx's response.

## Inbound webhook

`POST /webhooks/telnyx/` (new `TelnyxWebhookView`, alongside the existing `FreeSwitchWebhookView` pattern in `apps/webhooks/`)

- Verifies Telnyx's Ed25519 webhook signature using `TELNYX_PUBLIC_KEY` (via the `cryptography` package — already a dependency, no new install needed).
- `message.sent` / `message.finalized` → update `Message.status`/timestamps by `telnyx_message_id`.
- `message.received` → resolve/create `Conversation` + participants, create the inbound `Message`.
- Follow-up (not first pass): push inbound messages to the frontend in real time via `OutboxEvent` → WebSocket, following the same pattern as `extension.updated` in `apps/webhooks/views.py`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/messaging/conversations/` | List threads for the tenant (participants, last message) |
| GET | `/api/v1/messaging/conversations/{id}/messages/` | Thread history |
| POST | `/api/v1/messaging/messages/send/` | Send outbound message (1:1 or group) |
| POST | `/webhooks/telnyx/` | Inbound Telnyx webhook (signature-verified, `AllowAny`) |

## Supporting changes

- `Tenant.telnyx_messaging_profile_id` field + migration.
- `apps/common/services/telnyx_client.py` — `httpx`-based client mirroring `FreeSwitchClientService.proxy_request`.
- Settings/`.env.example`: `TELNYX_API_BASE_URL`, `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY`, `TELNYX_API_TIMEOUT_SECONDS`.

## Open questions for implementation time

- Does an outbound send always resolve the conversation server-side from the recipient list, or can the client pass an explicit `conversation_id`?
- Confirm exact Telnyx group-MMS payload shape (webhook `to` array format, `message.received` participant fields) against a live test send before finalizing serializer field names — same approach used to confirm CDR field names against real sample data.

## Phase 2: RCS (not in this build)

- Requires Telnyx/Google **RCS Agent** registration and brand approval (business process, has its own lead time — plan separately).
- Adds `Message.channel` (`sms`/`mms`/`rcs`) and `Message.rich_content` (JSON: cards, carousels, quick-reply chips/buttons).
- Adds capability discovery / fallback handling (RCS → SMS/MMS when recipient or carrier isn't RCS-capable) — Telnyx's Messaging Profile can handle this automatically, but the actual channel used per message still needs to be recorded.
- New webhook events: read receipts, RCS-specific delivery failure reasons, optionally typing indicators.
- Possible `Tenant.telnyx_rcs_agent_id` (or profile-level RCS enablement — needs confirming against Telnyx's current API).
