# 🛒 Telegram Marketplace Bot

A robust, asynchronous Telegram bot designed to connect buyers and sellers. It features a complete marketplace lifecycle, admin dashboard, multi-language support (English/Amharic), and a highly reliable background processing system using the Transactional Outbox pattern.

## ✨ Features

- **Buyer Flow**: Post purchase requests with images, track statuses, and select from seller offers.
- **Seller Flow**: Register, get admin approval, receive broadcasted requests, and submit price offers.
- **Admin Dashboard**: Full control panel to approve requests/sellers, manage users, handle support tickets, and settle transactions.
- **Reliability**: Uses the **Transactional Outbox Pattern** and **Idempotency Keys** to ensure no messages are lost and no actions are duplicated.
- **Background Processing**: Heavy tasks (like broadcasting to thousands of sellers) are offloaded to an ARQ background worker.
- **Clean Architecture**: Domain-Driven Design (DDD) separation of concerns (Core, Services, Models, Bot UI, Infrastructure).
- **i18n Support**: Fully localized in English and Amharic.

---

## 🏗️ Architecture Overview

The project follows a **Clean Architecture / Domain-Driven Design** approach:

```text
app/
├── config/          # Environment variables & Pydantic settings
├── db/              # SQLAlchemy engine, sessions, and base models
├── models/          # ORM models grouped by domain (user, marketplace, system)
├── core/            # Pure business rules, state machines, exceptions, utils
├── services/        # Use cases & database queries (orchestration layer)
├── bot/             # Telegram UI rendering, handlers, and routing
├── i18n/            # Translation dictionaries and manager
└── infrastructure/  # Redis state manager & ARQ background worker