# app/models/enums.py
# LAYER: Domain / Models
# PURPOSE: Single source of truth for all system statuses and states.
# WHY HERE: Enums are shared across multiple models. Keeping them separate prevents circular imports.

import enum


class UserStatus(str, enum.Enum): ACTIVE = "ACTIVE"; SUSPENDED = "SUSPENDED"
class SellerApprovalStatus(str, enum.Enum): PENDING = "PENDING"; APPROVED = "APPROVED"; DECLINED = "DECLINED"

# STATE MACHINE ENUM: Tracks the exact lifecycle of a purchase request
class RequestStatus(str, enum.Enum):
    PENDING_ADMIN_APPROVAL = "PENDING_ADMIN_APPROVAL"; DECLINED = "DECLINED"; APPROVED = "APPROVED"
    BROADCASTING = "BROADCASTING"; COLLECTING_SELLERS = "COLLECTING_SELLERS"; COLLECTING_OFFERS = "COLLECTING_OFFERS"
    BUYER_SELECTING = "BUYER_SELECTING"; SELLER_SELECTED = "SELLER_SELECTED"; ADMIN_SETTLEMENT = "ADMIN_SETTLEMENT"
    CLOSED = "CLOSED"; CANCELLED = "CANCELLED"

class RequestSellerStatus(str, enum.Enum): NOTIFIED = "NOTIFIED"; ACCEPTED = "ACCEPTED"; REJECTED = "REJECTED"; OFFER_SUBMITTED = "OFFER_SUBMITTED"; EXPIRED = "EXPIRED"
class OfferStatus(str, enum.Enum): ACTIVE = "ACTIVE"; SELECTED = "SELECTED"; NOT_SELECTED = "NOT_SELECTED"; WITHDRAWN = "WITHDRAWN"
class TicketStatus(str, enum.Enum): OPEN = "OPEN"; IN_PROGRESS = "IN_PROGRESS"; RESOLVED = "RESOLVED"; CLOSED = "CLOSED"
class SuspensionStatus(str, enum.Enum): ACTIVE = "ACTIVE"; LIFTED = "LIFTED"
class IdempotencyStatus(str, enum.Enum): PROCESSING = "PROCESSING"; COMPLETED = "COMPLETED"; FAILED = "FAILED"
class OutboxStatus(str, enum.Enum): PENDING = "PENDING"; PROCESSING = "PROCESSING"; DONE = "DONE"; FAILED = "FAILED"; DEAD_LETTER = "DEAD_LETTER"
class NotificationStatus(str, enum.Enum): PENDING = "PENDING"; PROCESSING = "PROCESSING"; SENT = "SENT"; FAILED = "FAILED"; DEAD_LETTER = "DEAD_LETTER"
class BroadcastJobStatus(str, enum.Enum): PENDING = "PENDING"; RUNNING = "RUNNING"; COMPLETED = "COMPLETED"; FAILED = "FAILED"