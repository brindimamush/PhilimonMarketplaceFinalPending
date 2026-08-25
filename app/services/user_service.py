# app/services/user_service.py
# LAYER: Application / Services
# PURPOSE: Handles user lookup, registration, and profile management.

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User as TelegramUser

from app.config.settings import get_settings
from app.core.exceptions import LocalizedDomainError, SuspendedError
from app.core.utils import normalize_ethiopian_phone, utcnow, write_audit
from app.i18n import supported_language
from app.models import BuyerProfile, SellerApprovalStatus, SellerProfile, User, UserStatus
from app.services.system_service import notification_payload, notify_admins

settings = get_settings()


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def get_or_create_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    """
    Fetches an existing user or creates a new one.

    Invariant:
    One Telegram ID = one platform user.
    """
    user = await get_user_by_telegram_id(session, tg_user.id)

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language=settings.default_language,
            language_set=False,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        await write_audit(
            session,
            action="USER_FIRST_SEEN",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
        )
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.last_seen_at = utcnow()

    return user


async def get_buyer_profile(session: AsyncSession, user_id: uuid.UUID) -> BuyerProfile | None:
    return await session.scalar(select(BuyerProfile).where(BuyerProfile.user_id == user_id))


async def get_seller_profile(session: AsyncSession, user_id: uuid.UUID) -> SellerProfile | None:
    return await session.scalar(select(SellerProfile).where(SellerProfile.user_id == user_id))

async def set_user_language(
    session: AsyncSession,
    tg_user: TelegramUser,
    code: str,
) -> User:
    """
    Persists the user's language preference.

    Spec requirement:
    Language preference must be persisted and future messages must use it.
    Admin UI remains English at presentation level.
    """
    user = await get_or_create_user(session, tg_user)
    normalized = supported_language(code)

    before = {"language": user.language}
    user.language = normalized
    user.language_set = True

    await write_audit(
        session,
        action="USER_LANGUAGE_CHANGED",
        actor_user_id=user.id,
        actor_role="user",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after={"language": normalized},
    )

    return user

async def register_buyer(
    session: AsyncSession,
    tg_user: TelegramUser,
    phone_number: str,
    full_name: str,
) -> BuyerProfile:
    """
    Registers a buyer profile idempotently.

    Invariant:
    One user = at most one buyer profile.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise SuspendedError("suspended.message")

    existing = await get_buyer_profile(session, user.id)
    if existing:
        return existing

    normalized_phone = normalize_ethiopian_phone(phone_number)
    normalized_name = " ".join((full_name or "").split())

    if len(normalized_name) < settings.min_full_name_length:
        raise LocalizedDomainError("error.invalid_full_name")

    # Keep the canonical phone number on the core user identity as well.
    user.phone_number = normalized_phone

    profile = BuyerProfile(
        user_id=user.id,
        full_name=normalized_name,
        phone_number=normalized_phone,
        phone_verified=True,
        terms_version="v1",
    )

    session.add(profile)
    await session.flush()

    await write_audit(
        session,
        action="BUYER_REGISTERED",
        actor_user_id=user.id,
        actor_role="buyer",
        entity_type="buyer_profile",
        entity_id=profile.id,
    )

    return profile


async def register_seller_application(
    session: AsyncSession,
    tg_user: TelegramUser,
    phone_number: str | None,
    full_name: str | None,
    business_name: str,
    location: str,
    product_category: str,
    shop_number: str,
) -> SellerProfile:
    """
    Registers or updates a seller application.

    Invariant:
    One user = at most one seller profile.

    If the user already has a seller profile:
    - APPROVED profiles are returned unchanged.
    - PENDING or DECLINED profiles are updated, not duplicated.
    """
    user = await get_or_create_user(session, tg_user)

    if user.status == UserStatus.SUSPENDED:
        raise SuspendedError("suspended.message")

    buyer = await get_buyer_profile(session, user.id)

    final_phone = phone_number or (buyer.phone_number if buyer else None)
    final_name = " ".join((full_name or (buyer.full_name if buyer else "")).split())

    if not final_phone:
        raise LocalizedDomainError("error.ethiopian_phone")

    final_phone = normalize_ethiopian_phone(final_phone)

    # Keep the canonical phone number on the core user identity as well.
    user.phone_number = final_phone

    if len(final_name) < settings.min_full_name_length:
        raise LocalizedDomainError("error.invalid_full_name")
    
    business_name = " ".join((business_name or "").split())
    location = " ".join((location or "").split())
    product_category = " ".join((product_category or "").split())
    shop_number = " ".join((shop_number or "").split())

    if len(business_name) < 2:
        raise LocalizedDomainError("error.invalid_business_name")

    if len(location) < 2:
        raise LocalizedDomainError("error.invalid_location")

    if len(product_category) < 2:
        raise LocalizedDomainError("error.invalid_category")

    if len(shop_number) < 2:
        raise LocalizedDomainError("error.invalid_shop_number")

    if len(business_name) > 255:
        business_name = business_name[:255]

    if len(location) > 255:
        location = location[:255]

    if len(product_category) > 255:
        product_category = product_category[:255]

    if len(shop_number) > 120:
        shop_number = shop_number[:120]

    existing = await session.scalar(
        select(SellerProfile)
        .where(SellerProfile.user_id == user.id)
        .with_for_update()
    )

    if existing and existing.approval_status == SellerApprovalStatus.APPROVED:
        return existing

    if existing:
        existing.full_name = final_name
        existing.phone_number = final_phone
        existing.business_name = business_name
        existing.location = location
        existing.product_category = product_category
        existing.shop_number = shop_number
        existing.approval_status = SellerApprovalStatus.PENDING
        existing.approved_by = None
        existing.approved_at = None
        existing.terms_version = "v1"

        profile = existing
        audit_action = "SELLER_APPLICATION_UPDATED"
    else:
        profile = SellerProfile(
            user_id=user.id,
            full_name=final_name,
            phone_number=final_phone,
            business_name=business_name,
            location=location,
            product_category=product_category,
            shop_number=shop_number,
            approval_status=SellerApprovalStatus.PENDING,
            terms_version="v1",
        )
        session.add(profile)
        audit_action = "SELLER_APPLICATION_CREATED"

    await session.flush()

    await write_audit(
        session,
        action=audit_action,
        actor_user_id=user.id,
        entity_type="seller_profile",
        entity_id=profile.id,
    )

    admin_text = "\n".join(
        [
            "🆕 New Seller Application",
            "",
            f"Application ID: {profile.id}",
            f"Submitted: {utcnow().isoformat()}",
            f"Name: {final_name}",
            f"Phone: {final_phone}",
            f"Username: @{tg_user.username}" if tg_user.username else "Username: -",
            f"Telegram ID: {tg_user.id}",
            "",
            f"Business: {business_name}",
            f"Location: {location}",
            f"Category: {product_category}",
            f"Shop Number: {shop_number}",
        ]
    )

    buttons = [
        [
            {"text": "Approve", "callback_data": f"seller_app:approve:{user.id}"},
            {"text": "Decline", "callback_data": f"seller_app:decline:{user.id}"},
        ],
        [
            {"text": "Edit", "callback_data": f"seller_app:edit:{user.id}"},
        ],
    ]

    await notify_admins(session, text=admin_text, buttons=buttons)

    return profile