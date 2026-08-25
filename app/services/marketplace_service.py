# app/services/marketplace_service.py
# LAYER: Application / Services
# PURPOSE: Core business logic for Purchase Requests and Offers.

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User as TelegramUser

from app.config.settings import get_settings
from app.core.exceptions import ConflictError, DomainError, LocalizedDomainError, NotFoundError
from app.core.state_machine import assert_transition
from app.core.utils import utcnow, write_audit
from app.i18n import get_text, supported_language
from app.models import (
    BroadcastJob,
    BroadcastJobStatus,
    BuyerProfile,
    NotificationDelivery,
    OfferStatus,
    PurchaseRequest,
    RequestSeller,
    RequestSellerStatus,
    RequestStatus,
    SellerApprovalStatus,
    SellerOffer,
    SellerProfile,
    User,
    UserStatus,
)
from app.services.system_service import enqueue_outbox, notification_payload, notify_admins
from app.services.user_service import get_buyer_profile, get_or_create_user, get_seller_profile

settings = get_settings()

MAX_PENDING_BUYER_REQUESTS = settings.max_pending_buyer_requests
MAX_ACCEPTED_SELLERS = settings.max_accepted_sellers


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _parse_price(raw) -> Decimal:
    """
    Converts user input into a safe Decimal price.

    Money must never be stored as float.
    """
    if isinstance(raw, Decimal):
        price = raw
    else:
        cleaned = str(raw or "").replace(",", "").strip()
        if not cleaned:
            raise LocalizedDomainError("error.invalid_price")
        try:
            price = Decimal(cleaned)
        except (InvalidOperation, ValueError):
            raise LocalizedDomainError("error.invalid_price")

    if price <= 0:
        raise LocalizedDomainError("error.price_positive")

    if price > Decimal("999999999999.99"):
        raise LocalizedDomainError("error.price_too_large")

    return price.quantize(Decimal("0.01"))


def _paginate(page: int, page_size: int, total: int) -> tuple[int, int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 10))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    return page, total_pages, (page - 1) * page_size


async def get_buyer_requests_page(
    session: AsyncSession,
    user_id,
    page: int = 1,
    page_size: int = 5,
):
    """
    Returns the authenticated buyer's request list.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(PurchaseRequest)
        .where(PurchaseRequest.buyer_id == user_id)
    ) or 0

    page, total_pages, offset = _paginate(page, page_size, total)

    requests = (
        await session.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.buyer_id == user_id)
            .order_by(PurchaseRequest.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()

    return page, total_pages, total, requests


async def get_seller_offers_page(
    session: AsyncSession,
    user_id,
    page: int = 1,
    page_size: int = 5,
):
    """
    Returns the authenticated seller's submitted offers.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(SellerOffer)
        .where(SellerOffer.seller_id == user_id)
    ) or 0

    page, total_pages, offset = _paginate(page, page_size, total)

    rows = (
        await session.execute(
            select(SellerOffer, PurchaseRequest)
            .join(PurchaseRequest, SellerOffer.request_id == PurchaseRequest.id)
            .where(SellerOffer.seller_id == user_id)
            .order_by(SellerOffer.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()

    return page, total_pages, total, rows

async def create_purchase_request(
    session: AsyncSession,
    tg_user: TelegramUser,
    image_file_id: str,
    image_unique_id: str | None,
    quantity: int,
    description: str,
) -> PurchaseRequest:
    """
    Creates a buyer purchase request.

    Business rules:
    - Buyer profile required.
    - Suspended users blocked.
    - Maximum 3 pending admin approval requests.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    buyer = await get_buyer_profile(session, user.id)
    if not buyer:
        raise LocalizedDomainError("error.register_buyer_first")

    description = (description or "").strip()
    if len(description) < 5:
        raise LocalizedDomainError("error.invalid_description")
    if len(description) > 2000:
        description = description[:2000]

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise LocalizedDomainError("error.invalid_quantity")

    if quantity < 1:
        raise LocalizedDomainError("error.invalid_quantity")

    pending_count = await session.scalar(
        select(func.count())
        .select_from(PurchaseRequest)
        .where(
            PurchaseRequest.buyer_id == user.id,
            PurchaseRequest.status == RequestStatus.PENDING_ADMIN_APPROVAL,
        )
    ) or 0

    if pending_count >= MAX_PENDING_BUYER_REQUESTS:
        raise LocalizedDomainError("error.max_pending_requests", max=MAX_PENDING_BUYER_REQUESTS)

    request = PurchaseRequest(
        buyer_id=user.id,
        request_number=f"REQ-{uuid.uuid4().hex[:6].upper()}",
        image_file_id=image_file_id,
        image_unique_id=image_unique_id,
        quantity=quantity,
        description=description,
        status=RequestStatus.PENDING_ADMIN_APPROVAL,
    )
    session.add(request)
    await session.flush()

    await write_audit(
        session,
        action="REQUEST_CREATED",
        actor_user_id=user.id,
        actor_role="buyer",
        entity_type="purchase_request",
        entity_id=request.id,
    )

    admin_text = "\n".join(
        [
            "🆕 New Purchase Request",
            f"Request: {request.request_number}",
            f"Buyer: {buyer.full_name}",
            f"Phone: {buyer.phone_number}",
            f"Telegram ID: {tg_user.id}",
            f"Username: @{tg_user.username}" if tg_user.username else "Username: -",
            f"Description: {description}",
            f"Quantity: {quantity}",
            "Status: Pending Approval",
        ]
    )

    buttons = [
        [
            {"text": "Approve", "callback_data": f"request:approve:{request.id}"},
            {"text": "Decline", "callback_data": f"request:decline:{request.id}"},
        ]
    ]

    await notify_admins(session, text=admin_text, photo_file_id=image_file_id, buttons=buttons)

    return request


async def approve_request(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    request_id: uuid.UUID | str,
) -> PurchaseRequest:
    """
    Admin approves a buyer request.

    Idempotency:
    - If already approved, return existing request.
    - If already declined/processed, raise conflict.
    """
    admin_user = await get_or_create_user(session, admin_tg_user)
    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)

    if not request:
        raise NotFoundError("Request not found.")

    if request.status == RequestStatus.APPROVED:
        return request

    if request.status != RequestStatus.PENDING_ADMIN_APPROVAL:
        raise ConflictError("Already processed.")

    assert_transition(request.status, RequestStatus.APPROVED)

    request.status = RequestStatus.APPROVED
    request.approved_by = admin_user.id
    request.approved_at = utcnow()

    await session.flush()

    await write_audit(
        session,
        action="REQUEST_APPROVED",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="purchase_request",
        entity_id=request.id,
        before={"status": RequestStatus.PENDING_ADMIN_APPROVAL.value},
        after={"status": RequestStatus.APPROVED.value},
    )

    await enqueue_outbox(session, "request.broadcast", {"request_id": str(request.id)})

    return request


async def decline_request(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    request_id: uuid.UUID | str,
    reason: str,
) -> PurchaseRequest:
    """
    Admin declines a buyer request.

    Requirement:
    - Decline reason is mandatory.
    - Action must be idempotent.
    """
    admin_user = await get_or_create_user(session, admin_tg_user)
    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)

    if not request:
        raise NotFoundError("Request not found.")

    if request.status == RequestStatus.DECLINED:
        return request

    if request.status != RequestStatus.PENDING_ADMIN_APPROVAL:
        raise ConflictError("Already processed.")

    reason = (reason or "").strip()
    if len(reason) < settings.decline_reason_min_length:
        raise DomainError("Reason must be at least 3 characters.")

    assert_transition(request.status, RequestStatus.DECLINED)

    request.status = RequestStatus.DECLINED
    request.declined_by = admin_user.id
    request.declined_at = utcnow()
    request.decline_reason = reason

    await session.flush()

    await write_audit(
        session,
        action="REQUEST_DECLINED",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="purchase_request",
        entity_id=request.id,
        before={"status": RequestStatus.PENDING_ADMIN_APPROVAL.value},
        after={"status": RequestStatus.DECLINED.value},
        metadata={"reason": reason},
    )

    buyer_user = await session.get(User, request.buyer_id)
    if buyer_user:
        await enqueue_outbox(
            session,
            "notification.telegram",
            notification_payload(
                buyer_user.telegram_id,
                get_text(
                    buyer_user.language,
                    "request.declined_buyer",
                    request_number=request.request_number,
                    reason=reason,
                ),
            ),
        )

    return request


async def start_broadcast(session: AsyncSession, request_id: uuid.UUID | str) -> BroadcastJob | None:
    """
    Starts broadcasting an approved request to eligible sellers.

    Reliability:
    - Creates BroadcastJob.
    - Creates NotificationDelivery rows.
    - Worker sends Telegram messages asynchronously.
    """
    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)

    if not request:
        raise NotFoundError("Request not found.")

    existing_job = await session.scalar(
        select(BroadcastJob).where(BroadcastJob.request_id == request.id)
    )
    if existing_job:
        return existing_job

    if request.status == RequestStatus.APPROVED:
        assert_transition(request.status, RequestStatus.BROADCASTING)
        request.status = RequestStatus.BROADCASTING
    elif request.status != RequestStatus.BROADCASTING:
        return None

    sellers_result = await session.execute(
        select(User)
        .join(SellerProfile, SellerProfile.user_id == User.id)
        .where(User.status == UserStatus.ACTIVE)
        .where(SellerProfile.approval_status == SellerApprovalStatus.APPROVED)
        .where(User.id != request.buyer_id)
    )
    sellers = sellers_result.scalars().all()

    job = BroadcastJob(
        request_id=request.id,
        status=BroadcastJobStatus.RUNNING,
        total_recipients=len(sellers),
    )
    session.add(job)
    await session.flush()

    for seller in sellers:
        lang = supported_language(seller.language)

        text = get_text(
            lang,
            "broadcast.new_request",
            request_number=request.request_number,
            quantity=request.quantity,
            description=request.description or "",
        )

        buttons = [
            [
                {"text": get_text(lang, "buttons.accept"), "callback_data": f"seller:accept:{request.id}"},
                {"text": get_text(lang, "buttons.reject"), "callback_data": f"seller:reject:{request.id}"},
            ]
        ]

        session.add(
            NotificationDelivery(
                user_id=seller.id,
                type="request_broadcast",
                payload=notification_payload(seller.telegram_id, text, request.image_file_id, buttons),
                broadcast_job_id=job.id,
            )
        )

    assert_transition(request.status, RequestStatus.COLLECTING_SELLERS)
    request.status = RequestStatus.COLLECTING_SELLERS

    if not sellers:
        job.status = BroadcastJobStatus.COMPLETED

    await write_audit(
        session,
        action="REQUEST_BROADCAST_STARTED",
        actor_role="system",
        entity_type="purchase_request",
        entity_id=request.id,
        after={"recipients": len(sellers)},
    )

    return job


async def seller_accept_request(
    session: AsyncSession,
    tg_user: TelegramUser,
    request_id: uuid.UUID | str,
) -> RequestSeller:
    """
    Seller accepts a broadcasted request.

    Critical invariant:
    Maximum 3 accepted sellers per request.

    Concurrency protection:
    The request row is locked before checking/incrementing accepted_seller_count.
    Without this lock, parallel Telegram callbacks could allow more than 3 sellers.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    seller = await get_seller_profile(session, user.id)
    if not seller or seller.approval_status != SellerApprovalStatus.APPROVED:
        raise LocalizedDomainError("error.only_approved_sellers")

    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)
    if not request:
        raise NotFoundError("Request not found.")

    if request.buyer_id == user.id:
        raise LocalizedDomainError("error.cannot_accept_own_request")

    if request.status not in (RequestStatus.BROADCASTING, RequestStatus.COLLECTING_SELLERS):
        raise LocalizedDomainError("error.request_not_accepting_sellers")

    request_seller = await session.scalar(
        select(RequestSeller)
        .where(
            RequestSeller.request_id == request.id,
            RequestSeller.seller_id == user.id,
        )
        .with_for_update()
    )

    if request_seller:
        if request_seller.status == RequestSellerStatus.ACCEPTED:
            return request_seller

        if request_seller.status == RequestSellerStatus.OFFER_SUBMITTED:
            raise LocalizedDomainError("error.already_submitted_offer")

        if request_seller.status == RequestSellerStatus.REJECTED:
            raise LocalizedDomainError("error.already_rejected")

    if request.accepted_seller_count >= MAX_ACCEPTED_SELLERS:
        raise LocalizedDomainError("error.seller_capacity_full")

    if not request_seller:
        request_seller = RequestSeller(
            request_id=request.id,
            seller_id=user.id,
            status=RequestSellerStatus.NOTIFIED,
        )

    request_seller.status = RequestSellerStatus.ACCEPTED
    request_seller.accepted_at = utcnow()
    request_seller.rejected_at = None

    request.accepted_seller_count += 1

    session.add(request_seller)
    await session.flush()

    await write_audit(
        session,
        action="SELLER_ACCEPTED",
        actor_user_id=user.id,
        actor_role="seller",
        entity_type="request_seller",
        entity_id=request_seller.id,
        after={"request_id": str(request.id), "accepted_count": request.accepted_seller_count},
    )

    return request_seller


async def seller_reject_request(
    session: AsyncSession,
    tg_user: TelegramUser,
    request_id: uuid.UUID | str,
) -> RequestSeller:
    """
    Seller rejects a broadcasted request.

    Idempotent:
    Repeated rejection returns the existing rejection.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    seller = await get_seller_profile(session, user.id)
    if not seller or seller.approval_status != SellerApprovalStatus.APPROVED:
        raise LocalizedDomainError("error.only_approved_sellers")

    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)
    if not request:
        raise NotFoundError("Request not found.")

    if request.status not in (RequestStatus.BROADCASTING, RequestStatus.COLLECTING_SELLERS):
        raise LocalizedDomainError("error.request_not_accepting_sellers")

    request_seller = await session.scalar(
        select(RequestSeller)
        .where(
            RequestSeller.request_id == request.id,
            RequestSeller.seller_id == user.id,
        )
        .with_for_update()
    )

    if request_seller:
        if request_seller.status == RequestSellerStatus.REJECTED:
            return request_seller

        if request_seller.status in (
            RequestSellerStatus.ACCEPTED,
            RequestSellerStatus.OFFER_SUBMITTED,
        ):
            raise ConflictError("Already accepted.")

    if not request_seller:
        request_seller = RequestSeller(
            request_id=request.id,
            seller_id=user.id,
            status=RequestSellerStatus.NOTIFIED,
        )

    request_seller.status = RequestSellerStatus.REJECTED
    request_seller.rejected_at = utcnow()
    request_seller.accepted_at = None

    session.add(request_seller)
    await session.flush()

    await write_audit(
        session,
        action="SELLER_REJECTED",
        actor_user_id=user.id,
        actor_role="seller",
        entity_type="request_seller",
        entity_id=request_seller.id,
        after={"request_id": str(request.id)},
    )

    return request_seller


async def submit_seller_offer(
    session: AsyncSession,
    tg_user: TelegramUser,
    request_id: uuid.UUID | str,
    raw_price: str,
) -> SellerOffer:
    """
    Seller submits one offer for an accepted request.

    Invariants:
    - One seller = at most one offer per request.
    - Price must be positive Decimal.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    request = await session.get(PurchaseRequest, _as_uuid(request_id), with_for_update=True)
    if not request:
        raise NotFoundError("Request not found.")

    if request.status not in (
        RequestStatus.COLLECTING_SELLERS,
        RequestStatus.COLLECTING_OFFERS,
        RequestStatus.BUYER_SELECTING,
    ):
        raise LocalizedDomainError("error.request_not_accepting_offers")

    request_seller = await session.scalar(
        select(RequestSeller)
        .where(
            RequestSeller.request_id == request.id,
            RequestSeller.seller_id == user.id,
        )
        .with_for_update()
    )

    if not request_seller:
        raise LocalizedDomainError("error.not_accepted_seller")

    if request_seller.status == RequestSellerStatus.OFFER_SUBMITTED:
        raise LocalizedDomainError("error.already_submitted_offer")

    if request_seller.status != RequestSellerStatus.ACCEPTED:
        raise LocalizedDomainError("error.not_accepted_seller")

    existing_offer = await session.scalar(
        select(SellerOffer)
        .where(SellerOffer.request_seller_id == request_seller.id)
        .with_for_update()
    )
    if existing_offer:
        raise LocalizedDomainError("error.already_submitted_offer")

    price = _parse_price(raw_price)

    offer = SellerOffer(
        request_id=request.id,
        seller_id=user.id,
        request_seller_id=request_seller.id,
        price=price,
        currency="ETB",
        status=OfferStatus.ACTIVE,
    )

    request_seller.status = RequestSellerStatus.OFFER_SUBMITTED
    request_seller.offer_submitted_at = utcnow()

    if request.status == RequestStatus.COLLECTING_SELLERS:
        assert_transition(request.status, RequestStatus.COLLECTING_OFFERS)
        request.status = RequestStatus.COLLECTING_OFFERS

    session.add(offer)
    await session.flush()

    await write_audit(
        session,
        action="OFFER_SUBMITTED",
        actor_user_id=user.id,
        actor_role="seller",
        entity_type="seller_offer",
        entity_id=offer.id,
        after={"request_id": str(request.id), "price": str(offer.price)},
    )

    buyer_user = await session.get(User, request.buyer_id)
    if buyer_user:
        lang = supported_language(buyer_user.language)

        text = "\n".join(
            [
                get_text(
                    lang,
                    "offer.new_for_buyer",
                    request_number=request.request_number,
                    price=f"{offer.price:,.2f}",
                    currency=offer.currency,
                ),
                f"{get_text(lang, 'request.quantity')}: {request.quantity}",
                request.description or "",
            ]
        )

        buttons = [
            [
                {
                    "text": get_text(lang, "buttons.choose_this_offer"),
                    "callback_data": f"offer:confirm:{offer.id}",
                },
                {
                    "text": get_text(lang, "buttons.choose_another"),
                    "callback_data": f"request:offers:{request.id}",
                },
            ]
        ]

        await enqueue_outbox(
            session,
            "notification.telegram",
            notification_payload(buyer_user.telegram_id, text, request.image_file_id, buttons),
        )

    return offer


async def get_active_offers_for_request(
    session: AsyncSession,
    tg_user: TelegramUser,
    request_id: uuid.UUID | str,
):
    """
    Buyer views active offers.

    Privacy:
    Seller identity is not returned to the buyer.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    request = await session.get(PurchaseRequest, _as_uuid(request_id))
    if not request:
        raise NotFoundError("Request not found.")

    if request.buyer_id != user.id:
        raise LocalizedDomainError("error.request_not_yours")

    result = await session.execute(
        select(SellerOffer)
        .where(
            SellerOffer.request_id == request.id,
            SellerOffer.status == OfferStatus.ACTIVE,
        )
        .order_by(SellerOffer.price.asc())
    )

    return request, result.scalars().all()


async def select_offer(
    session: AsyncSession,
    tg_user: TelegramUser,
    offer_id: uuid.UUID | str,
) -> PurchaseRequest:
    """
    Buyer selects exactly one offer.

    Invariant:
    One request = maximum one selected offer.

    The request row and offer rows are locked to prevent concurrent selection.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise LocalizedDomainError("suspended.message")

    offer = await session.get(SellerOffer, _as_uuid(offer_id), with_for_update=True)
    if not offer:
        raise NotFoundError("Offer not found.")

    request = await session.get(PurchaseRequest, offer.request_id, with_for_update=True)
    if not request:
        raise NotFoundError("Request not found.")

    if request.buyer_id != user.id:
        raise LocalizedDomainError("error.request_not_yours")

    if request.status in (
        RequestStatus.CLOSED,
        RequestStatus.CANCELLED,
        RequestStatus.DECLINED,
    ):
        raise LocalizedDomainError("error.request_already_processed")

    selected_offer = await session.scalar(
        select(SellerOffer)
        .where(
            SellerOffer.request_id == request.id,
            SellerOffer.status == OfferStatus.SELECTED,
        )
        .with_for_update()
    )

    if selected_offer:
        if selected_offer.id == offer.id:
            return request
        raise LocalizedDomainError("error.offer_no_longer_selectable")

    if offer.status != OfferStatus.ACTIVE:
        raise LocalizedDomainError("error.offer_not_available")

    if request.status not in (
        RequestStatus.COLLECTING_OFFERS,
        RequestStatus.BUYER_SELECTING,
        RequestStatus.SELLER_SELECTED,
        RequestStatus.ADMIN_SETTLEMENT,
    ):
        raise LocalizedDomainError("error.offer_not_ready")

    offer.status = OfferStatus.SELECTED

    await session.execute(
        update(SellerOffer)
        .where(
            SellerOffer.request_id == request.id,
            SellerOffer.id != offer.id,
            SellerOffer.status == OfferStatus.ACTIVE,
        )
        .values(status=OfferStatus.NOT_SELECTED)
    )

    request.selected_offer_id = offer.id

    if request.status != RequestStatus.ADMIN_SETTLEMENT:
        assert_transition(request.status, RequestStatus.ADMIN_SETTLEMENT)
        request.status = RequestStatus.ADMIN_SETTLEMENT

    await session.flush()

    await write_audit(
        session,
        action="OFFER_SELECTED",
        actor_user_id=user.id,
        actor_role="buyer",
        entity_type="purchase_request",
        entity_id=request.id,
        after={"selected_offer_id": str(offer.id), "status": request.status.value},
    )

    seller_user = await session.get(User, offer.seller_id)
    seller_profile = await get_seller_profile(session, offer.seller_id)
    buyer_profile = await get_buyer_profile(session, request.buyer_id)
    buyer_user = await session.get(User, request.buyer_id)

    if seller_user:
        await enqueue_outbox(
            session,
            "notification.telegram",
            notification_payload(
                seller_user.telegram_id,
                get_text(
                    supported_language(seller_user.language),
                    "offer.selected_seller",
                    request_number=request.request_number,
                    price=f"{offer.price:,.2f}",
                    currency=offer.currency,
                ),
            ),
        )

    admin_text = "\n".join(
        [
            "💰 Offer Selected",
            f"Request: {request.request_number}",
            f"Buyer: {buyer_profile.full_name if buyer_profile else 'Unknown'}",
            f"Buyer Username: @{buyer_user.username}" if buyer_user and buyer_user.username else "Buyer Username: -",
            f"Buyer Phone: {buyer_profile.phone_number if buyer_profile else '-'}",
            f"Buyer Telegram ID: {buyer_user.telegram_id if buyer_user else '-'}",
            f"Description: {request.description}",
            f"Quantity: {request.quantity}",
            f"Selected Offer: {offer.price:,.2f} {offer.currency}",
            f"Seller: {seller_profile.full_name if seller_profile else 'Unknown'}",
            f"Seller Username: @{seller_user.username}" if seller_user and seller_user.username else "Seller Username: -",
            f"Seller Phone: {seller_profile.phone_number if seller_profile else '-'}",
            f"Seller Telegram ID: {seller_user.telegram_id if seller_user else '-'}",
            f"Business: {seller_profile.business_name if seller_profile else '-'}",
            f"Location: {seller_profile.location if seller_profile else '-'}",
            f"Category: {seller_profile.product_category if seller_profile else '-'}",
            f"Shop Number: {seller_profile.shop_number if seller_profile else '-'}",
        ]
    )

    buttons = [
        [
            {"text": "Pending", "callback_data": f"settle:pending:{request.id}"},
            {"text": "Settled", "callback_data": f"settle:settled:{request.id}"},
            {"text": "Cancel", "callback_data": f"settle:canceled:{request.id}"},
        ]
    ]

    await notify_admins(session, text=admin_text, photo_file_id=request.image_file_id, buttons=buttons)

    return request