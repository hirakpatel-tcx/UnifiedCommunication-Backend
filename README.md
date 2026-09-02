# Unified Communication Backend

[![Python](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-6.1-green.svg)](https://www.djangoproject.com/)
[![Django REST Framework](https://img.shields.io/badge/DRF-3.18%2B-red.svg)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7%2B-dc382d.svg)](https://redis.io/)

A modern, multi-tenant Unified Communication (UC) backend platform integrating voice telephony (FreeSWITCH), messaging, FaxBox management, shared voicemail boxes, Call Detail Records (CDR), and reliable real-time event streaming via WebSockets.

---

## 📑 Table of Contents

- [Architecture Highlights](#-architecture-highlights)
- [Tech Stack](#-tech-stack)
- [Project Directory Structure](#-project-directory-structure)
- [Data Models & Entities](#-data-models--entities)
- [Local Development Setup](#-local-development-setup)
- [API Documentation](#-api-documentation)
- [Security & Isolation Principles](#-security--isolation-principles)

---

## 🏛 Architecture Highlights

- **Strict Multi-Tenancy**: Every client query is tenant-isolated. Cross-tenant data leakage is prevented at the model and queryset layers.
- **Identity & Credential Separation**: 
  - Application login uses email + Argon2/PBKDF2 one-way hashed passwords.
  - Telephony registration uses SIP username + reversible Fernet-encrypted SIP passwords (`SecretService`).
- **Reliable WebSocket Delivery (Outbox Pattern)**: Events are written to the database atomically with state changes, then published to Redis channel layers by background workers.
- **Dual Logging Strategy**:
  - `AuditLog`: Permanent, append-only security and mutation tracking for user and admin actions.
  - `WebhookLog`: 48-hour temporary store for debugging inbound FreeSWITCH events with indexed automated cleanup.
- **Fax via FaxBox Only**: Direct Inward Dial (DID) numbers handle calling and messaging. Faxing is decoupled into dedicated, sharable `FaxBox` structures.

---

## 💻 Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.13+ |
| **Web Framework** | Django 6.1+ |
| **API Framework** | Django REST Framework (DRF) + SimpleJWT |
| **Realtime / WebSockets** | Django Channels + Channels-Redis + Daphne |
| **Database** | PostgreSQL with `psycopg3` (async-ready) |
| **Cache / Broker** | Redis + `django-redis` |
| **Async Task Queue** | Celery |
| **Cryptography** | `cryptography` (Fernet symmetric encryption) |

---

## 📂 Project Directory Structure

```text
UnifiedCommunication-Backend/
├── apps/
│   ├── audit/          # Permanent audit logging (AuditLog)
│   ├── common/         # Base models (UUIDModel, TimestampedModel)
│   ├── dids/           # DID management & UserDID assignment through-tables
│   ├── extensions/     # FreeSWITCH SIP Extensions
│   ├── outbox/         # Reliable WebSocket dispatch queue (OutboxEvent)
│   ├── tenants/        # Multi-tenant definitions & feature toggles
│   ├── users/          # Custom User model (globally unique emails, JSON mailboxes)
│   ├── voicemail/      # Per-user shared mailbox read states (VoicemailReadState)
│   └── webhooks/       # Inbound FreeSWITCH event logs with 48h TTL
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── asgi.py         # Channels ASGI entry point
│   ├── wsgi.py         # WSGI entry point
│   └── urls.py
├── docs/
│   └── api_documentation.md   # Complete REST & WebSocket API specification
├── requirements/
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
├── .env.example
├── .gitignore
└── manage.py
```

---

## 🗄 Data Models & Entities

| Model | Table | Responsibility |
|---|---|---|
| **`Tenant`** | `tenants` | Top-level tenant. Stores stable FreeSWITCH tenant UUID, default `sip_domain`, per-tenant encrypted API key, and enabled feature flags (`calling`, `messaging`, `fax`, `voicemail`). |
| **`User`** | `users` | Globally unique normalized email, application password, optional per-user `sip_domain` override, role (`superadmin`, `admin`, `user`), and JSON routing lists: `fax_boxes` and `voicemail_boxes`. |
| **`Extension`** | `extensions` | Telephony SIP extension (`101`), `transport_type` (`UDP`, `TCP`, `TLS`, `DTLS`), and encrypted SIP password. 0..1 relationship to `User`. |
| **`DID`** | `dids` | Phone numbers in E.164 format. Owned by the Tenant and mapped to users. |
| **`UserDID`** | `user_dids` | Grants user access to a DID. Allows multiple users to share numbers while ownership remains strictly with the Tenant. |
| **`WebhookLog`** | `webhook_logs` | Temporary 48-hour event log for inbound FreeSWITCH webhooks. Sanitized payloads, indexed `expires_at`. |
| **`AuditLog`** | `audit_logs` | Permanent append-only audit trail recording user, admin, and system mutations. |
| **`OutboxEvent`** | `outbox_events` | Decoupled database-to-WebSocket queue with explicit targeting (`user` vs `tenant`). |

> **Note on Voicemail & Fax Data**: Voicemail messages, audio recordings, transcripts, read states, and fax documents remain exclusively in FreeSWITCH. The database does **not** store communication artifacts. The backend's responsibility is purely **resource-to-user event routing** using `User.voicemail_boxes` and `User.fax_boxes`.

---

## 🚀 Local Development Setup

### 1. Prerequisites
- Python 3.13+ installed
- PostgreSQL 16+ running locally
- Redis 7+ running locally

### 2. Clone and Setup Environment
```bash
# Clone the repository
git clone https://github.com/hirak536/UnifiedCommunication-Backend.git
cd UnifiedCommunication-Backend

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements/development.txt
```

### 3. Configure Environment Variables
Copy the sample environment file:
```bash
cp .env.example .env
```

Generate a Fernet encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edit `.env` and set:
```ini
SECRET_KEY=your-django-secret-key
DEBUG=True
DATABASE_URL=postgres://your_user:your_password@localhost:5432/your_database
REDIS_URL=redis://localhost:6379/0
ENCRYPTION_KEY=your-generated-fernet-key
```

### 4. Run Migrations & Create Superuser
```bash
# Apply migrations to PostgreSQL
python manage.py migrate

# Create a platform superadmin (Django Admin access)
python manage.py createsuperuser
```

### 5. Start Development Server
```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/` in your browser to access the Django Administration interface.

---

## 📖 API Documentation

Complete REST and WebSocket specifications, payload samples, and authentication details are maintained in:
📄 **[`docs/api_documentation.md`](docs/api_documentation.md)**

---

## 🔐 Security & Isolation Principles

- **Zero Plaintext Secrets**: Sensitive attributes (FreeSWITCH API keys, SIP passwords) are encrypted at rest using Fernet symmetric encryption and decrypted strictly in-memory during authorized handshakes.
- **Audit Immutability**: `AuditLog` records are strictly append-only. Modification and deletion are disabled in application code and admin interfaces.
- **Inbound Webhook Sanitization**: All inbound FreeSWITCH payloads are scrubbed of plain API keys and passwords before persistence.
- **Email Normalization**: User emails are trimmed and lowercased before validation and storage, preventing cross-tenant collision attacks.