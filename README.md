
```markdown
# 🛒 Telegram Marketplace Bot

A production-oriented asynchronous Telegram bot that connects buyers and sellers in a managed marketplace workflow.

The bot supports:

- buyer registration
- seller registration and approval
- purchase request creation with image upload
- seller offers
- buyer offer selection
- admin settlement
- support tickets with back-and-forth conversation
- multilingual UI
- audit logging
- idempotent actions
- background processing using the Transactional Outbox pattern
- easy switching between **long polling** and **webhook**

---

## ✨ Core Features

### 👤 User Features

- Buyer registration
- Seller registration/application
- Purchase request creation with image upload
- Buyer request dashboard
- Seller offer submission
- Seller offer dashboard
- Offer selection by buyer
- Support ticket creation and follow-up
- Language selection: English and Amharic

---

## 🎧 Support Ticket Behavior

Support is now conversation-based.

### User side

- User runs `/support`
- If no active ticket exists:
  - bot asks for a description
  - a new ticket is created
- If an active ticket already exists:
  - bot tells the user to reply directly
  - plain text messages are appended to the active ticket

### Admin side

Admin receives ticket notifications with:

- `View Ticket`
- `Respond`
- `Close Ticket`

### Respond

- Admin can send multiple responses
- Every response is stored in the ticket thread
- User is notified immediately
- User can reply back
- Admin is notified of every user reply

### Close

- Closing is only allowed after at least one admin response
- Admin must provide a clear solution
- The solution is stored
- The user is notified with the solution

---

## 🏗 Architecture

The project follows a Clean Architecture / Domain-Driven Design style.

```text
app/
├── bot/                  # Telegram handlers, middleware, UI flows
├── config/               # Environment settings
├── core/                 # Domain rules, exceptions, utilities
├── db/                   # SQLAlchemy base and session management
├── i18n/                 # English and Amharic translations
├── infrastructure/       # Redis state, logging, background worker
├── models/               # SQLAlchemy ORM models
└── services/             # Application/business services
```

---

## 📦 Core Reliability Patterns

### ✅ Transactional Outbox

External side effects such as Telegram notifications are written to an `outbox_events` table inside the same database transaction as the main business operation.

A background worker later processes those events.

This ensures:

- database state and notifications do not drift apart
- messages are retried on failure
- broadcasts are processed asynchronously

---

### ✅ Idempotency

State-changing operations use idempotency keys.

This protects against:

- duplicate Telegram callbacks
- network retries
- double-clicks
- repeated user actions

---

### ✅ State Machine

Purchase requests follow a strict lifecycle.

Invalid transitions are rejected.

---

### ✅ Audit Logging

Important actions are written to `audit_logs`.

Audited actions include:

- user first seen
- buyer registration
- seller application creation/update
- seller approval/decline
- request approval/decline
- offer submission
- offer selection
- support responses
- support closure
- suspension/lifting

---

## 🧰 Tech Stack

- Python 3.13+
- python-telegram-bot
- SQLAlchemy 2.x async
- asyncpg
- Alembic
- PostgreSQL
- Redis
- ARQ background worker
- Pydantic Settings
- structlog
- orjson
- phonenumbers
- Pillow

---

# 🚀 Getting Started

## 1. Clone the repository

```bash
git clone your-repository-url
cd marketplace-bot
```

---

## 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

---

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Configure environment variables

Create a `.env` file in the project root.

Example:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

REDIS_URL=redis://localhost:6379/0

POSTGRES_USER=marketplace
POSTGRES_PASSWORD=marketplace
POSTGRES_DB=marketplace
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

ADMIN_TELEGRAM_IDS=123456789,987654321

BOT_MODE=polling
DROP_PENDING_UPDATES=false
```

---

## 5. Run database migrations

```bash
alembic upgrade head
```

---

## 6. Start the bot

```bash
python main.py
```

---

## 7. Start the background worker

The worker processes:

- outbox events
- broadcast jobs
- notification deliveries

Run:

```bash
python worker_main.py
```

Both processes should run together:

```bash
python main.py
python worker_main.py
```

---

# 📡 Long Polling vs Webhook

This bot supports **two transport modes**:

- `polling` — easiest for local development
- `webhook` — recommended for production

Switching is done using one environment variable:

```env
BOT_MODE=polling
```

or

```env
BOT_MODE=webhook
```

No code changes are needed.

---

## Option A: Long Polling

This is the simplest mode.

### `.env`

```env
BOT_MODE=polling
```

### Run

```bash
python main.py
python worker_main.py
```

### When to use polling

Use polling for:

- local development
- quick testing
- environments where you do not have HTTPS / public domain yet

---

## Option B: Webhook

Use webhook mode when you want a production setup.

Telegram will send updates to your server via HTTPS.

---

### Telegram webhook requirements

Telegram requires:

- HTTPS
- a publicly reachable URL
- one of these ports:
  - `443`
  - `80`
  - `88`
  - `8443`

---

### Webhook `.env` example

```env
BOT_MODE=webhook

PUBLIC_WEBHOOK_URL=https://bot.example.com/webhook/CHANGE_ME
WEBHOOK_LISTEN_HOST=0.0.0.0
WEBHOOK_LISTEN_PORT=8443
WEBHOOK_SECRET_TOKEN=CHANGE_ME_TO_A_LONG_RANDOM_VALUE

DROP_PENDING_UPDATES=false
```

If your public URL is:

```text
https://bot.example.com/webhook/CHANGE_ME
```

then the bot will automatically use:

```text
webhook/CHANGE_ME
```

as the local webhook path.

You can override it manually if needed:

```env
WEBHOOK_PATH=webhook/CHANGE_ME
```

---

## Webhook deployment patterns

### Pattern 1: Reverse proxy terminates TLS (recommended)

This is the most common production setup:

```text
Telegram
   ↓ HTTPS
Nginx / Caddy / Traefik
   ↓ HTTP
Bot webhook server on port 8443
```

In this case:

- leave `WEBHOOK_CERT_PATH` empty
- leave `WEBHOOK_KEY_PATH` empty
- let Nginx/Caddy handle TLS

---

### Nginx example

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;

    ssl_certificate /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location /webhook/ {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

Then run:

```bash
python main.py
python worker_main.py
```

---

### Pattern 2: Bot terminates TLS directly

If you want the bot itself to serve HTTPS, set:

```env
WEBHOOK_CERT_PATH=/path/to/cert.pem
WEBHOOK_KEY_PATH=/path/to/key.pem
```

Then run:

```bash
python main.py
python worker_main.py
```

For production, use a trusted certificate.

---

## How the mode switch works

The entrypoint reads:

```env
BOT_MODE=polling
```

or

```env
BOT_MODE=webhook
```

Then:

- if `polling`:
  - `app.run_polling(...)` is used
  - webhook is removed automatically
- if `webhook`:
  - `app.run_webhook(...)` is used
  - Telegram update URL is registered automatically

You do **not** need to modify handler code to switch modes.

---

# 🧑‍💼 Admin Setup

Set admin Telegram IDs in `.env`:

```env
ADMIN_TELEGRAM_IDS=123456789
```

For multiple admins:

```env
ADMIN_TELEGRAM_IDS=123456789,987654321
```

Admins can use:

```text
/admin
/search
```

---

# 📱 Bot Commands

## General Commands

| Command | Description |
|---|---|
| `/start` | Open dashboard/menu |
| `/menu` | Same as `/start` |
| `/language` | Change language |
| `/support` | Contact support |

---

## Buyer Commands

| Command | Description |
|---|---|
| `/newrequest` | Create a new purchase request |
| `/myrequests` | View my purchase requests |

---

## Seller Commands

| Command | Description |
|---|---|
| `/registerseller` | Apply to become a seller |
| `/myoffers` | View my submitted offers |

---

## Admin Commands

| Command | Description |
|---|---|
| `/admin` | Open admin dashboard |
| `/search` | Search users or requests |

Example search usage:

```text
/search username
/search 0912345678
/search 123456789
/search REQ-ABC123
```

---

# 🛡 Safety and Access Rules

## Registration Requirements

Protected commands require proper registration:

| Command | Requirement |
|---|---|
| `/newrequest` | Must be registered as a buyer |
| `/myrequests` | Must be registered as a buyer |
| `/myoffers` | Must be an approved seller |
| `/registerseller` | Available to active users |
| `/support` | Available even to suspended users |

---

## Suspended Users

Suspended users are blocked from marketplace actions.

They can still:

- use `/support`
- open a support ticket
- reply to an active support ticket
- continue an active support workflow

This allows suspended users to contact support and appeal their suspension.

---

## Admin Self-Suspension Protection

Admins are protected from accidentally suspending their own account.

The system blocks self-suspension at multiple levels:

1. UI level
2. workflow level
3. service level

---

# 🐞 Troubleshooting

## Bot does not respond

Check:

- bot token is correct
- bot process is running
- network access to Telegram API is available

---

## Admin dashboard says `Not authorized`

Ensure your Telegram ID is included in:

```env
ADMIN_TELEGRAM_IDS=your_telegram_id
```

For multiple admins:

```env
ADMIN_TELEGRAM_IDS=111111111,222222222
```

---

## Broadcasts are not sent

Ensure the worker is running:

```bash
python worker_main.py
```

Also check:

- Redis is running
- `outbox_events` table has pending events
- `notification_deliveries` table has pending records

---

## Database migration errors

Run:

```bash
alembic upgrade head
```

If Alembic cannot find models, ensure `app.models` is imported in `alembic/env.py`.

---

## Webhook does not receive updates

Check:

- `BOT_MODE=webhook`
- `PUBLIC_WEBHOOK_URL` is correct
- public URL is HTTPS
- domain is publicly reachable
- port is allowed by Telegram
- reverse proxy forwards the path correctly
- bot process is running

You can inspect webhook info with:

```bash
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo
```

---

# 🧱 Design Principles

This project is built around the following principles:

- clean separation of concerns
- domain-driven boundaries
- explicit state transitions
- idempotent operations
- transactional consistency
- retry-safe background processing
- auditability
- multilingual user experience
- Telegram-native UX
- easy deployment mode switching

---

## 📄 License


```text
MIT
```
```
