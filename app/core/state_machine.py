# app/core/state_machine.py
# LAYER: Domain / Core
# PURPOSE: Enforces valid state transitions for Purchase Requests.

from app.core.exceptions import DomainError
from app.models.enums import RequestStatus

ALLOWED_REQUEST_TRANSITIONS = {
    RequestStatus.PENDING_ADMIN_APPROVAL: {
        RequestStatus.DECLINED,
        RequestStatus.APPROVED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.DECLINED: set(),
    RequestStatus.APPROVED: {
        RequestStatus.BROADCASTING,
        RequestStatus.COLLECTING_SELLERS,
        RequestStatus.CANCELLED,
    },
    RequestStatus.BROADCASTING: {
        RequestStatus.COLLECTING_SELLERS,
        RequestStatus.CANCELLED,
    },
    RequestStatus.COLLECTING_SELLERS: {
        RequestStatus.COLLECTING_OFFERS,
        RequestStatus.BUYER_SELECTING,
        RequestStatus.ADMIN_SETTLEMENT,
        RequestStatus.CANCELLED,
    },
    RequestStatus.COLLECTING_OFFERS: {
        RequestStatus.BUYER_SELECTING,
        RequestStatus.SELLER_SELECTED,
        RequestStatus.ADMIN_SETTLEMENT,
        RequestStatus.CANCELLED,
    },
    RequestStatus.BUYER_SELECTING: {
        RequestStatus.SELLER_SELECTED,
        RequestStatus.ADMIN_SETTLEMENT,
        RequestStatus.CANCELLED,
    },
    RequestStatus.SELLER_SELECTED: {
        RequestStatus.ADMIN_SETTLEMENT,
        RequestStatus.CANCELLED,
    },
    RequestStatus.ADMIN_SETTLEMENT: {
        RequestStatus.CLOSED,
        RequestStatus.CANCELLED,
    },
    RequestStatus.CLOSED: set(),
    RequestStatus.CANCELLED: set(),
}


def assert_transition(current: RequestStatus, new: RequestStatus) -> None:
    """
    Validates if a request state transition is legal.

    This prevents invalid lifecycle jumps such as:
    DECLINED -> APPROVED
    CLOSED -> ADMIN_SETTLEMENT
    """
    if current == new:
        return

    allowed = ALLOWED_REQUEST_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise DomainError(f"Invalid request transition: {current.value} -> {new.value}")