# app/services/support_service.py
# LAYER: Application / Services
# PURPOSE: Support ticket creation and admin response handling.

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User as TelegramUser

from app.config.settings import get_settings
from app.core.exceptions import ConflictError, DomainError, LocalizedDomainError, NotFoundError
from app.core.utils import utcnow, write_audit
from app.models import SupportTicket, TicketStatus
from app.services.system_service import notify_admins
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    get_seller_profile,
)

settings = get_settings()


async def create_support_ticket(
    session: AsyncSession,
    tg_user: TelegramUser,
    description: str,
) -> SupportTicket:
    """
    Creates a support ticket.

    Suspended users must still be able to contact support.
    Therefore this function intentionally does not block suspended users.
    """
    user = await get_or_create_user(session, tg_user)

    description = (description or "").strip()
    if len(description) < settings.support_min_length:
        raise LocalizedDomainError("error.support_description_short")

    ticket = SupportTicket(
        ticket_number=f"SUP-{uuid.uuid4().hex[:6].upper()}",
        user_id=user.id,
        description=description,
        status=TicketStatus.OPEN,
    )

    session.add(ticket)
    await session.flush()

    await write_audit(
        session,
        action="SUPPORT_CREATED",
        actor_user_id=user.id,
        actor_role="user",
        entity_type="support_ticket",
        entity_id=ticket.id,
    )

    buyer = await get_buyer_profile(session, user.id)
    seller = await get_seller_profile(session, user.id)

    name = (
        buyer.full_name
        if buyer and buyer.full_name
        else seller.full_name
        if seller and seller.full_name
        else tg_user.full_name or "Unknown"
    )

    phone = (
        user.phone_number
        or (buyer.phone_number if buyer else None)
        or (seller.phone_number if seller else None)
        or "-"
    )

    admin_text = "\n".join(
        [
            "🆘 New Support Request",
            f"Ticket: {ticket.ticket_number}",
            f"User: {name}",
            f"Username: @{tg_user.username}" if tg_user.username else "Username: -",
            f"Phone: {phone}",
            f"Telegram ID: {tg_user.id}",
            f"Problem: {description}",
        ]
    )

    buttons = [
        [
            {
                "text": "View Ticket",
                "callback_data": f"support:view:{ticket.id}",
            },
            {
                "text": "Respond",
                "callback_data": f"support:respond:{ticket.id}",
            },
        ],
        [
            {
                "text": "Close Ticket",
                "callback_data": f"support:close:{ticket.id}",
            },
        ],
    ]
    
    await notify_admins(session, text=admin_text, buttons=buttons)

    return ticket


async def respond_support_ticket(
    session: AsyncSession,
    admin_tg_user: TelegramUser,
    ticket_id: uuid.UUID,
    response: str,
) -> SupportTicket:
    """
    Admin responds to a ticket.

    Ticket moves to IN_PROGRESS.
    """
    admin_user = await get_or_create_user(session, admin_tg_user)

    ticket = await session.get(SupportTicket, ticket_id, with_for_update=True)
    if not ticket:
        raise NotFoundError("Support ticket not found.")

    if ticket.status == TicketStatus.CLOSED:
        raise ConflictError("This ticket is already closed.")

    response = (response or "").strip()
    if len(response) < settings.support_min_length:
        raise DomainError("Response must be at least 5 characters.")

    ticket.status = TicketStatus.IN_PROGRESS
    ticket.admin_response = response

    await write_audit(
        session,
        action="SUPPORT_RESPONDED",
        actor_user_id=admin_user.id,
        actor_role="admin",
        entity_type="support_ticket",
        entity_id=ticket.id,
        after={"status": ticket.status.value},
    )

    return ticket