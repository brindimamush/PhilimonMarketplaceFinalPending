# app/services/admin_service.py
# LAYER: Application / Services (Admin & Support Domain)
# PURPOSE: Handles administrative actions (suspending users, settling requests, 
# closing tickets) and admin dashboard queries (pagination, search, counts).
# WHY HERE: Keeps admin-specific business logic isolated from core marketplace transactions.

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User as TelegramUser

from app.config.settings import get_settings
from app.core.exceptions import (
    ConflictError,
    DomainError,
    LocalizedDomainError,
    NotFoundError,
)
from app.core.state_machine import assert_transition
from app.core.utils import utcnow, write_audit
from app.i18n import get_text, status_text
from app.models import (
    BuyerProfile,
    OfferStatus,
    PurchaseRequest,
    RequestSeller,
    RequestSellerStatus,
    RequestStatus,
    SellerApprovalStatus,
    SellerOffer,
    SellerProfile,
    SupportTicket,
    SuspensionStatus,
    TicketStatus,
    User,
    UserStatus,
    UserSuspension,
)
from app.services.system_service import enqueue_outbox, notification_payload
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    get_seller_profile,
    get_user_by_telegram_id,
)

settings = get_settings()

# --- User Management ---

async def suspend_user_by_telegram_id(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    target_telegram_id: int,
    reason: str | None,
) -> User:
    admin_user = await get_or_create_user(session, admin_tg_user)

    # Prevent admin from suspending their own Telegram account.
    if int(target_telegram_id) == int(admin_tg_user.id):
        raise DomainError("You cannot suspend your own account.")

    target = await get_user_by_telegram_id(session, target_telegram_id)
    if not target:
        raise NotFoundError("User not found.")

    # Extra safety: also compare internal DB user IDs.
    if target.id == admin_user.id:
        raise DomainError("You cannot suspend your own account.")

    if target.status == UserStatus.SUSPENDED:
        return target

    before = {"status": target.status.value}

    target.status = UserStatus.SUSPENDED

    session.add(
        UserSuspension(
            user_id=target.id,
            reason=reason,
            suspended_by=admin_user.id,
            status=SuspensionStatus.ACTIVE,
        )
    )

    await write_audit(
        session,
        action="USER_SUSPENDED",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="user",
        entity_id=target.id,
        before=before,
        after={"status": target.status.value},
        metadata={"reason": reason},
    )

    await enqueue_outbox(
        session,
        "notification.telegram",
        notification_payload(
            target.telegram_id,
            get_text(target.language, "suspended.message"),
        ),
    )

    return target

async def lift_user_suspension_by_telegram_id(session: AsyncSession, admin_tg_user: TelegramUser, target_telegram_id: int) -> User:
    admin_user = await get_or_create_user(session, admin_tg_user)
    target = await get_user_by_telegram_id(session, target_telegram_id)
    if not target: raise NotFoundError("User not found.")
    if target.status == UserStatus.ACTIVE: return target

    before = {"status": target.status.value}
    target.status = UserStatus.ACTIVE
    suspension = await session.scalar(select(UserSuspension).where(UserSuspension.user_id == target.id, UserSuspension.status == SuspensionStatus.ACTIVE).order_by(UserSuspension.created_at.desc()).limit(1).with_for_update())
    if suspension:
        suspension.status = SuspensionStatus.LIFTED
        suspension.lifted_by = admin_user.id
        suspension.lifted_at = utcnow()

    await write_audit(session, action="USER_UNSUSPENDED", actor_user_id=admin_user.id, actor_role="admin", entity_type="user", entity_id=target.id, before=before, after={"status": target.status.value})
    await enqueue_outbox(session, "notification.telegram", notification_payload(target.telegram_id, get_text(target.language, "suspension.lifted")))
    return target

# --- Support Tickets ---

async def close_support_ticket(session: AsyncSession, admin_tg_user: TelegramUser, ticket_id: uuid.UUID, solution: str) -> SupportTicket:
    admin_user = await get_or_create_user(session, admin_tg_user)
    ticket = await session.get(SupportTicket, ticket_id, with_for_update=True)
    if not ticket: raise NotFoundError("Support ticket not found.")
    if ticket.status == TicketStatus.CLOSED:
        return ticket
    # ENFORCEMENT: Spec requires tickets to be IN_PROGRESS before closing
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise DomainError("Ticket must be responded to (In Progress) before it can be closed.")
    solution = (solution or "").strip()
    if len(solution) < 5: raise DomainError("Solution must be at least 5 characters.")
    if len(solution) > 2000: solution = solution[:2000]

    before = {"status": ticket.status.value}
    ticket.status = TicketStatus.CLOSED
    ticket.solution = solution
    ticket.closed_by = admin_user.id
    ticket.closed_at = utcnow()

    await write_audit(session, action="SUPPORT_TICKET_CLOSED", actor_user_id=admin_user.id, actor_role="admin", entity_type="support_ticket", entity_id=ticket.id, before=before, after={"status": ticket.status.value}, metadata={"solution": solution})
    
    ticket_owner = await session.get(User, ticket.user_id)
    if ticket_owner:
        await enqueue_outbox(session, "notification.telegram", notification_payload(ticket_owner.telegram_id, get_text(ticket_owner.language, "support.closed_user", ticket_number=ticket.ticket_number, solution=solution)))
    return ticket

# --- Admin Dashboard Queries ---

async def get_admin_dashboard_counts(session: AsyncSession) -> dict:
    return {
        "users_total": await session.scalar(select(func.count()).select_from(User)) or 0,
        "requests_pending": await session.scalar(select(func.count()).select_from(PurchaseRequest).where(PurchaseRequest.status == RequestStatus.PENDING_ADMIN_APPROVAL)) or 0,
        "seller_applications_pending": await session.scalar(select(func.count()).select_from(SellerProfile).where(SellerProfile.approval_status == SellerApprovalStatus.PENDING)) or 0,
        "suspended_users": await session.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.SUSPENDED)) or 0,
        "support_open": await session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]))
        ) or 0,
    }

def _paginate(page: int, page_size: int, total: int) -> tuple[int, int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 10))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    return page, total_pages, (page - 1) * page_size

async def get_admin_users_page(session: AsyncSession, page: int = 1, page_size: int = 10, status: UserStatus | None = None):
    count_stmt = select(func.count()).select_from(User)
    if status: count_stmt = count_stmt.where(User.status == status)
    total = await session.scalar(count_stmt) or 0
    page, total_pages, offset = _paginate(page, page_size, total)

    stmt = select(User)
    if status: stmt = stmt.where(User.status == status)
    users = (await session.execute(stmt.order_by(User.created_at.desc()).offset(offset).limit(page_size))).scalars().all()
    
    # Fetch profiles to build labels
    user_ids = [u.id for u in users]
    buyers = {b.user_id: b for b in (await session.execute(select(BuyerProfile).where(BuyerProfile.user_id.in_(user_ids)))).scalars().all()}
    sellers = {s.user_id: s for s in (await session.execute(select(SellerProfile).where(SellerProfile.user_id.in_(user_ids)))).scalars().all()}

    items = []
    for user in users:
        buyer, seller = buyers.get(user.id), sellers.get(user.id)
        name = (buyer.full_name if buyer and buyer.full_name else seller.full_name if seller and seller.full_name else f"@{user.username}" if user.username else f"User {user.telegram_id}")
        items.append({"id": user.id, "label": " ".join(name.split())})
    return page, total_pages, total, items

async def get_admin_user_details(session: AsyncSession, user_id: uuid.UUID) -> dict:
    user = await session.get(User, user_id)
    if not user: raise NotFoundError("User not found.")
    buyer = await get_buyer_profile(session, user.id)
    seller = await get_seller_profile(session, user.id)
    
    active_suspension = await session.scalar(select(UserSuspension).where(UserSuspension.user_id == user.id, UserSuspension.status == SuspensionStatus.ACTIVE).order_by(UserSuspension.created_at.desc()).limit(1))
    
    display_name = buyer.full_name if buyer and buyer.full_name else seller.full_name if seller and seller.full_name else f"@{user.username}" if user.username else f"User {user.telegram_id}"
    phone = buyer.phone_number if buyer and buyer.phone_number else seller.phone_number if seller and seller.phone_number else None
    
    return {
        "user": user, "buyer": buyer, "seller": seller, "display_name": display_name, "phone": phone,
        "current_mode": "Buyer + Seller" if buyer and seller and seller.approval_status == SellerApprovalStatus.APPROVED else "Buyer" if buyer else "Seller" if seller else "Unregistered",
        "registration_at": buyer.registered_at if buyer else seller.created_at if seller else user.first_seen_at,
        "requests_count": await session.scalar(select(func.count()).select_from(PurchaseRequest).where(PurchaseRequest.buyer_id == user.id)) or 0,
        "participations_count": await session.scalar(select(func.count()).select_from(RequestSeller).where(RequestSeller.seller_id == user.id)) or 0,
        "offers_count": await session.scalar(select(func.count()).select_from(SellerOffer).where(SellerOffer.seller_id == user.id)) or 0,
        "tickets_count": await session.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.user_id == user.id)) or 0,
        "active_suspension": active_suspension
    }

# --- Admin Requests & Offers ---

async def get_admin_requests_page(session: AsyncSession, page: int = 1, page_size: int = 10):
    total = await session.scalar(select(func.count()).select_from(PurchaseRequest)) or 0
    page, total_pages, offset = _paginate(page, page_size, total)
    requests = (await session.execute(select(PurchaseRequest).order_by(PurchaseRequest.created_at.desc()).offset(offset).limit(page_size))).scalars().all()
    
    buyer_ids = [r.buyer_id for r in requests]
    buyers = {b.user_id: b for b in (await session.execute(select(BuyerProfile).where(BuyerProfile.user_id.in_(buyer_ids)))).scalars().all()}
    
    items = []
    for req in requests:
        buyer = buyers.get(req.buyer_id)
        name = buyer.full_name if buyer and buyer.full_name else "Unknown"
        items.append({"id": req.id, "label": f"{req.request_number} • {req.status.value} • {name}"})
    return page, total_pages, total, items

async def get_admin_request_details(session: AsyncSession, request_id: uuid.UUID) -> dict:
    request = await session.get(PurchaseRequest, request_id)
    if not request: raise NotFoundError("Request not found.")
    buyer_profile = await get_buyer_profile(session, request.buyer_id)
    buyer_user = await session.get(User, request.buyer_id)
    
    selected_offer = await session.scalar(select(SellerOffer).where(SellerOffer.request_id == request.id, SellerOffer.status == OfferStatus.SELECTED).limit(1))
    
    return {
        "request": request, "buyer_user": buyer_user, "buyer_profile": buyer_profile,
        "buyer_name": buyer_profile.full_name if buyer_profile else (f"@{buyer_user.username}" if buyer_user and buyer_user.username else "Unknown"),
        "phone": buyer_profile.phone_number if buyer_profile else None,
        "offers_count": await session.scalar(select(func.count()).select_from(SellerOffer).where(SellerOffer.request_id == request.id)) or 0,
        "selected_offer": selected_offer
    }

async def settle_request(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    request_id: uuid.UUID,
    action: str,
    reason: str,
) -> PurchaseRequest:
    """
    Admin manually settles a selected request.

    Actions:
    - pending
    - settled
    - canceled

    Cancellation requires a reason.
    """
    admin_user = await get_or_create_user(session, admin_tg_user)
    request = await session.get(PurchaseRequest, request_id, with_for_update=True)

    if not request:
        raise NotFoundError("Request not found.")

    action = (action or "").strip().lower()

    if action in {"settled", "closed"} and request.status == RequestStatus.CLOSED:
        return request

    if action in {"canceled", "cancelled"} and request.status == RequestStatus.CANCELLED:
        return request

    if request.status != RequestStatus.ADMIN_SETTLEMENT:
        raise DomainError("This request is not in settlement stage.")

    if action in {"canceled", "cancelled"}:
        reason = (reason or "").strip()
        if len(reason) < settings.decline_reason_min_length:
            raise DomainError("Cancellation reason is required.")

        assert_transition(request.status, RequestStatus.CANCELLED)
        request.status = RequestStatus.CANCELLED
        message_key = "settlement.message_cancelled"

    elif action in {"settled", "closed"}:
        reason = (reason or "").strip() or "Settled by admin."
        assert_transition(request.status, RequestStatus.CLOSED)
        request.status = RequestStatus.CLOSED
        message_key = "settlement.message_closed"

    elif action == "pending":
        reason = (reason or "").strip() or "Settlement pending."
        message_key = "settlement.message_pending"

    else:
        raise DomainError("Unknown settlement action.")

    request.settlement_reason = reason
    request.settlement_by = admin_user.id
    request.settlement_at = utcnow()

    await write_audit(
        session,
        action=f"REQUEST_SETTLEMENT_{action.upper()}",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="purchase_request",
        entity_id=request.id,
        before={"status": RequestStatus.ADMIN_SETTLEMENT.value},
        after={"status": request.status.value},
        metadata={"reason": reason},
    )

    selected_offer = await session.scalar(
        select(SellerOffer).where(
            SellerOffer.request_id == request.id,
            SellerOffer.status == OfferStatus.SELECTED,
        )
    )

    user_ids = {request.buyer_id}
    if selected_offer:
        user_ids.add(selected_offer.seller_id)

    for user_id in user_ids:
        target_user = await session.get(User, user_id)
        if target_user:
            await enqueue_outbox(
                session,
                "notification.telegram",
                notification_payload(
                    target_user.telegram_id,
                    get_text(
                        target_user.language,
                        message_key,
                        request_number=request.request_number,
                        reason=reason,
                    ),
                ),
            )

    return request

# --- Admin Search ---

async def admin_search(session: AsyncSession, query: str, limit: int = 5):
    raw = (query or "").strip()
    if not raw: return [], []

    # Search Requests
    requests = (await session.execute(select(PurchaseRequest).where(PurchaseRequest.request_number.ilike(f"%{raw.upper()}%")).limit(limit))).scalars().all()

    # Search Users
    user_filters = []
    stripped = raw.lstrip("@")
    if stripped.isdigit() and len(stripped) <= 18: user_filters.append(User.telegram_id == int(stripped))
    if stripped: user_filters.append(User.username.ilike(f"%{stripped}%"))
    
    phone_digits = re.sub(r"\D", "", raw)
    if phone_digits:
        variants = {phone_digits}
        if phone_digits.startswith("0"): variants.add("251" + phone_digits[1:])
        if phone_digits.startswith("9") and len(phone_digits) == 9: variants.add("251" + phone_digits)
        if phone_digits.startswith("251"): variants.add(phone_digits[3:])
        for v in variants:
            user_filters.append(User.phone_number.ilike(f"%{v}%"))
            user_filters.append(BuyerProfile.phone_number.ilike(f"%{v}%"))
            user_filters.append(SellerProfile.phone_number.ilike(f"%{v}%"))

    users = []
    if user_filters:
        users = (
            await session.execute(
                select(User)
                # FIX: Explicitly define the onclause for both outer joins 
                # to prevent SQLAlchemy AmbiguousForeignKeysError
                .outerjoin(BuyerProfile, BuyerProfile.user_id == User.id)
                .outerjoin(SellerProfile, SellerProfile.user_id == User.id)
                .where(or_(*user_filters))
                .order_by(User.created_at.desc())
                .limit(limit)
                .distinct()
            )
        ).scalars().all()

    user_ids = [u.id for u in users]

    buyers = {
        b.user_id: b
        for b in (
            await session.execute(select(BuyerProfile).where(BuyerProfile.user_id.in_(user_ids)))
        ).scalars().all()
    }

    sellers = {
        s.user_id: s
        for s in (
            await session.execute(select(SellerProfile).where(SellerProfile.user_id.in_(user_ids)))
        ).scalars().all()
    }

    user_results = []
    for user in users:
        buyer = buyers.get(user.id)
        seller = sellers.get(user.id)

        name = (
            buyer.full_name
            if buyer and buyer.full_name
            else seller.full_name
            if seller and seller.full_name
            else f"@{user.username}"
            if user.username
            else f"User {user.telegram_id}"
        )

        user_results.append({"id": user.id, "label": f"{name} • TG {user.telegram_id}"})
    return user_results, requests


async def approve_seller_application(session: AsyncSession, admin_tg_user: TelegramUser, target_user_id: uuid.UUID) -> SellerProfile:
    admin_user = await get_or_create_user(session, admin_tg_user)
    seller = await session.scalar(select(SellerProfile).where(SellerProfile.user_id == target_user_id).with_for_update())
    if not seller: raise NotFoundError("Seller profile not found.")
    if seller.approval_status == SellerApprovalStatus.APPROVED: return seller
    
    seller.approval_status = SellerApprovalStatus.APPROVED
    seller.approved_by = admin_user.id
    seller.approved_at = utcnow()
    await session.flush()
    
    await write_audit(session, action="SELLER_APPROVED", actor_user_id=admin_user.id, actor_role="admin", entity_type="seller_profile", entity_id=seller.id)
    
    target_user = await session.get(User, target_user_id)
    if target_user:
        await enqueue_outbox(session, "notification.telegram", notification_payload(target_user.telegram_id, get_text(target_user.language, "seller_app.approved")))
    return seller

async def decline_seller_application(session: AsyncSession, admin_tg_user: TelegramUser, target_user_id: uuid.UUID) -> SellerProfile:
    admin_user = await get_or_create_user(session, admin_tg_user)
    seller = await session.scalar(select(SellerProfile).where(SellerProfile.user_id == target_user_id).with_for_update())
    if not seller: raise NotFoundError("Seller profile not found.")
    if seller.approval_status == SellerApprovalStatus.DECLINED: return seller
    
    seller.approval_status = SellerApprovalStatus.DECLINED
    await session.flush()
    
    await write_audit(session, action="SELLER_DECLINED", actor_user_id=admin_user.id, actor_role="admin", entity_type="seller_profile", entity_id=seller.id)
    
    target_user = await session.get(User, target_user_id)
    if target_user:
        await enqueue_outbox(session, "notification.telegram", notification_payload(target_user.telegram_id, get_text(target_user.language, "seller_app.declined")))
    return seller

SELLER_EDITABLE_FIELDS = {
    "full_name",
    "business_name",
    "location",
    "product_category",
    "shop_number",
}


async def update_seller_application_field(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    target_user_id: uuid.UUID,
    field: str,
    value: str,
) -> SellerProfile:
    """
    Admin edits one editable seller application field.
    Spec:
    - Admin can edit name/business/location/category/shop number.
    - Admin cannot edit phone number.
    - All edits must be audited.

    Important:
    If the user already has a buyer profile and admin edits full_name,
    we also update BuyerProfile.full_name so the corrected name is not
    overridden by the buyer profile later.
    """
    admin_user = await get_or_create_user(session, admin_tg_user)

    seller = await session.scalar(
        select(SellerProfile)
        .where(SellerProfile.user_id == target_user_id)
        .with_for_update()
    )
    if not seller:
        raise NotFoundError("Seller profile not found.")

    if field not in SELLER_EDITABLE_FIELDS:
        raise DomainError("Field is not editable.")

    cleaned = " ".join((value or "").split())
    if len(cleaned) < 2:
        raise DomainError("Field value is too short.")

    if field == "shop_number" and len(cleaned) > 120:
        cleaned = cleaned[:120]
    elif len(cleaned) > 255:
        cleaned = cleaned[:255]

    before = {field: getattr(seller, field)}
    setattr(seller, field, cleaned)

    buyer_updated = False
    if field == "full_name":
        buyer = await get_buyer_profile(session, target_user_id)
        if buyer:
            buyer.full_name = cleaned
            buyer_updated = True

    await session.flush()

    await write_audit(
        session,
        action="SELLER_APPLICATION_EDITED",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="seller_profile",
        entity_id=seller.id,
        before=before,
        after={field: cleaned},
        metadata={"buyer_full_name_updated": buyer_updated} if buyer_updated else None,
    )

    return seller