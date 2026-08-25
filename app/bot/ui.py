# app/bot/ui.py
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n.manager import get_text


def build_main_menu_markup(
    has_buyer: bool,
    seller_approved: bool,
    is_admin: bool,
    show_seller_registration: bool = False,
) -> ReplyKeyboardMarkup:
    """
    Dynamically builds the persistent reply keyboard based on user roles.
    """
    rows = [
        [KeyboardButton("/start"), KeyboardButton("/menu")],
        [KeyboardButton("/support"), KeyboardButton("/language")],
    ]

    if has_buyer:
        rows.append([KeyboardButton("/newrequest"), KeyboardButton("/myrequests")])

    if show_seller_registration:
        rows.append([KeyboardButton("/registerseller")])

    if seller_approved:
        rows.append([KeyboardButton("/myoffers")])

    if is_admin:
        rows.append([KeyboardButton("/admin"), KeyboardButton("/search")])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def build_offer_list_markup(offers: list, lang: str) -> InlineKeyboardMarkup:
    """
    Generates inline buttons for a list of seller offers.

    Spec:
    Clicking an offer must first show a confirmation screen with the exact price.
    """
    rows = []

    for index, offer in enumerate(offers, start=1):
        label = get_text(
            lang,
            "offer.offer_label",
            index=index,
            price=f"{offer.price:,.2f}",
            currency=offer.currency,
        )

        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"offer:confirm:{offer.id}",
                )
            ]
        )

    return InlineKeyboardMarkup(rows)


def build_registration_markup(lang: str) -> InlineKeyboardMarkup:
    """
    Registration menu shown to users who have no buyer or seller profile.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.register_buyer"),
                    callback_data="register:buyer",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.register_seller"),
                    callback_data="register:seller",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.support"),
                    callback_data="support:open",
                )
            ],
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.change_language"),
                    callback_data="language:open",
                )
            ],
        ]
    )