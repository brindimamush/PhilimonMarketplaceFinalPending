
```markdown
# 🛒 Telegram Marketplace Bot

A production-oriented asynchronous Telegram bot that connects buyers and sellers in a managed marketplace workflow.

The bot supports buyer purchase requests, seller registration and approval, seller offers, buyer offer selection, admin settlement, support tickets, multilingual UI, audit logging, idempotent actions, and background processing using the Transactional Outbox pattern.

---

## ✨ Features

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

### 🛒 Buyer Flow

Buyers can:

1. Register as a buyer.
2. Create a purchase request:
   - Upload item image
   - Enter description
   - Enter quantity
   - Confirm and submit
3. Track request status.
4. View seller offers.
5. Select one offer.
6. Wait for admin settlement.

### 🏪 Seller Flow

Sellers can:

1. Submit a seller application.
2. Wait for admin approval.
3. Receive broadcasted approved purchase requests.
4. Accept or reject requests.
5. Submit a price offer.
6. Track submitted offers.
7. Get notified when their offer is selected.

### 🧑‍💼 Admin Features

Admins can:

- Open admin dashboard
- Search users, phone numbers, Telegram IDs, usernames, and request numbers
- Approve or decline buyer purchase requests
- Approve, decline, or edit seller applications
- Suspend users
- Lift user suspension
- Respond to support tickets
- Close support tickets
- Manage settlement:
  - Pending
  - Settled
  - Cancelled

---

## 🛡 Safety and Access Rules

### Registration Requirements

Protected commands require proper registration:

| Command | Requirement |
|---|---|
| `/newrequest` | Must be registered as a buyer |
| `/myrequests` | Must be registered as a buyer |
| `/myoffers` | Must be an approved seller |
| `/registerseller` | Available to active users |
| `/support` | Available even to suspended users |

If an unregistered user tries to use a protected command, the bot tells them to register first.

---

### Suspended Users

Suspended users are blocked from marketplace actions.

They can still:

- Use `/support`
- Open a support ticket
- Continue an active support workflow

This allows suspended users to contact support and appeal their suspension.

---

### Admin Self-Suspension Protection

Admins are protected from accidentally suspending their own account.

The system blocks self-suspension at multiple levels:

1. UI level:
   - Suspend button can be hidden or blocked for the admin's own profile.

2. Workflow level:
   - Suspension reason workflow is stopped if the target is the admin.

3. Service level:
   - `suspend_user_by_telegram_id()` rejects self-suspension even if the UI is bypassed.

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

- Database state and notifications do not drift apart.
- Messages are retried on failure.
- Broadcasts are processed asynchronously.

---

### ✅ Idempotency

State-changing operations use idempotency keys.

This protects against:

- Duplicate Telegram callbacks
- Network retries
- Double-clicks
- Repeated user actions

Examples:

- Buyer registration
- Seller application submission
- Purchase request creation
- Offer submission
- Offer selection
- Request approval
- Request decline
- Seller accept/reject
- Suspension lifting

---

### ✅ State Machine

Purchase requests follow a strict lifecycle.

Invalid transitions are rejected.

Example lifecycle:

```text
PENDING_ADMIN_APPROVAL
    ↓
APPROVED
    ↓
BROADCASTING
    ↓
COLLECTING_SELLERS
    ↓
COLLECTING_OFFERS
    ↓
BUYER_SELECTING
    ↓
SELLER_SELECTED
    ↓
ADMIN_SETTLEMENT
    ↓
CLOSED
```

Other terminal states:

```text
DECLINED
CANCELLED
```

---

### ✅ Audit Logging

Important actions are written to `audit_logs`.

Audited actions include:

- User first seen
- Buyer registration
- Seller application creation/update
- Seller approval/decline
- Seller application edits
- Request approval/decline
- Offer submission
- Offer selection
- User suspension
- Suspension lifting
- Support ticket creation/response/closure

---

### ✅ Rate Limiting

Redis-based rate limiting protects sensitive flows such as:

- Starting a new purchase request
- Uploading images

This helps prevent spam, accidental loops, and abuse.

---

### ✅ Image Validation

Buyer request images are validated before use.

The bot checks:

- File size
- Image decodability
- MIME type
- Allowed formats

Allowed formats:

- JPEG
- PNG
- WEBP

The bot does not trust file names or extensions.

---

### ✅ Phone Validation

Ethiopian phone numbers are validated and normalized using `phonenumbers`.

The canonical format stored is E.164.

Examples accepted:

- `+2519XXXXXXXX`
- `09XXXXXXXX`

---

## 🌐 Internationalization

Supported languages:

- English: `en`
- Amharic: `am`

Users can change language using:

```text
/language
```

Language preference is persisted in the database.

Admin-facing UI remains English.

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

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone your-repository-url
cd marketplace-bot
```

---

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

---

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

### 4. Configure environment variables

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

# Comma-separated Telegram admin IDs
ADMIN_TELEGRAM_IDS=123456789,987654321
```

---

### 5. Run database migrations

```bash
alembic upgrade head
```

---

### 6. Start the bot

```bash
python main.py
```

---

### 7. Start the background worker

The worker processes:

- Outbox events
- Broadcast jobs
- Notification deliveries

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

## 🧑‍💼 Admin Setup

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

## 📱 Bot Commands

### General Commands

| Command | Description |
|---|---|
| `/start` | Open dashboard/menu |
| `/menu` | Same as `/start` |
| `/language` | Change language |
| `/support` | Contact support |

---

### Buyer Commands

| Command | Description |
|---|---|
| `/newrequest` | Create a new purchase request |
| `/myrequests` | View my purchase requests |

---

### Seller Commands

| Command | Description |
|---|---|
| `/registerseller` | Apply to become a seller |
| `/myoffers` | View my submitted offers |

---

### Admin Commands

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

## 🔄 Marketplace Workflow

### Buyer Request Workflow

```text
/start
  ↓
Register as Buyer
  ↓
/newrequest
  ↓
Send item image
  ↓
Send description
  ↓
Send quantity
  ↓
Confirm and submit
  ↓
Admin approval
  ↓
Broadcast to sellers
  ↓
Sellers accept
  ↓
Sellers submit offers
  ↓
Buyer selects offer
  ↓
Admin settlement
```

---

### Seller Application Workflow

```text
/registerseller
  ↓
Accept seller rules
  ↓
Share phone number
  ↓
Enter full name if needed
  ↓
Enter business name
  ↓
Enter location
  ↓
Enter product category
  ↓
Enter shop number
  ↓
Confirm and submit
  ↓
Admin approval
```

If the user already has a buyer profile, the seller application reuses buyer phone and full name where appropriate.

---

### Admin Settlement Workflow

After a buyer selects an offer, admins receive a settlement panel.

Actions:

- `Pending`
- `Settled`
- `Cancel`

Cancellation requires a reason.

---

## 🗂 Database Models

### Core User Models

- `User`
- `BuyerProfile`
- `SellerProfile`
- `Role`
- `UserRole`

---

### Marketplace Models

- `PurchaseRequest`
- `RequestSeller`
- `SellerOffer`
- `SupportTicket`

---

### System Models

- `AuditLog`
- `IdempotencyRecord`
- `OutboxEvent`
- `NotificationDelivery`
- `BroadcastJob`
- `UserSuspension`
- `ConversationSession`

---

## 🔐 Security Notes

### Do not commit secrets

Never commit:

```text
.env
bot token
database password
redis credentials
```

The `.gitignore` should exclude sensitive files.

---

### Admin IDs

Only configured Telegram IDs can access admin features.

Admin authorization is checked in:

- Admin command handlers
- Admin callback handlers
- Admin text workflows

---

### Suspended user restrictions

Suspended users cannot:

- Create purchase requests
- View buyer dashboards
- Submit seller offers
- Accept broadcasted requests
- Continue marketplace workflows

They can still contact support.

---

## 🧪 Local Development Checklist

Before running:

```bash
alembic upgrade head
```

Ensure:

- PostgreSQL is running
- Redis is running
- `.env` exists
- `TELEGRAM_BOT_TOKEN` is valid
- `ADMIN_TELEGRAM_IDS` is set

Then run:

```bash
python main.py
python worker_main.py
```

---

## 🐞 Troubleshooting

### Bot does not respond

Check:

- Bot token is correct
- Bot is running
- Network access to Telegram API is available

---

### Admin dashboard says `Not authorized`

Ensure your Telegram ID is included in:

```env
ADMIN_TELEGRAM_IDS=your_telegram_id
```

For multiple admins:

```env
ADMIN_TELEGRAM_IDS=111111111,222222222
```

---

### Broadcasts are not sent

Ensure the worker is running:

```bash
python worker_main.py
```

Also check:

- Redis is running
- `outbox_events` table has pending events
- `notification_deliveries` table has pending records

---

### Database migration errors

Run:

```bash
alembic upgrade head
```

If Alembic cannot find models, ensure `app.models` is imported in `alembic/env.py`.

---

### Commands require registration

If a user sees:

```text
Register as a buyer first
```

or

```text
Register as a seller first
```

this is expected behavior.

The user must complete the relevant registration flow before using the command.

---

## 🧱 Design Principles

This project is built around the following principles:

- Clean separation of concerns
- Domain-driven boundaries
- Explicit state transitions
- Idempotent operations
- Transactional consistency
- Retry-safe background processing
- Auditability
- Multilingual user experience
- Telegram-native UX

---

## 📄 License

Add your license here.

Example:

```text
MIT
```
```