"""رندر کارت تأیید تراکنش و مدیریت state ویرایش.

حالت عادی کارت فقط دو خط است: «📝 عنوان» و «💰 مبلغ». اقلام و تگ‌ها فقط بعد از
زدن دکمه‌ی «جزئیات» روی همان کارت نشان داده می‌شوند.

State ویرایش در ``context.user_data["awaiting"]`` نگهداری می‌شود:
    {"action": "amount" | "title", "txn_id": <id>}
هر اکشن دکمه‌ای ``txn_id`` را در callback_data حمل می‌کند تا به کارت درستش بایند شود.
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.transactions import is_complete
from bot.utils.money import format_amount, to_persian_digits

AWAITING_KEY = "awaiting"
EXPANDED_KEY = "expanded_cards"

TITLE_EMOJI = "📝"
AMOUNT_EMOJI = "💰"


def render_card(txn: dict[str, Any], *, expanded: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    title = txn.get("title") or "— (تعیین‌نشده)"
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))

    lines = [f"{TITLE_EMOJI} {title}", f"{AMOUNT_EMOJI} {amount}"]

    if expanded:
        items = txn.get("mentioned_items") or []
        tags = txn.get("tags") or []
        if items:
            lines.append("🧺 " + "، ".join(items))
        if tags:
            lines.append("🏷 " + "، ".join(tags))
        if txn.get("note"):
            lines.append("🗒 " + txn["note"])

    if not is_complete(txn):
        lines.append("⚠️ برای ثبت، مبلغ و عنوان باید کامل باشند.")

    return "\n".join(lines), _keyboard(txn, expanded=expanded)


def _keyboard(txn: dict[str, Any], *, expanded: bool) -> InlineKeyboardMarkup:
    tid = txn["id"]
    has_extra = bool(txn.get("mentioned_items") or txn.get("tags") or txn.get("note"))
    rows = [
        [InlineKeyboardButton("✅ ثبت", callback_data=f"confirm:{tid}")],
        [
            InlineKeyboardButton("✏️ مبلغ", callback_data=f"editamt:{tid}"),
            InlineKeyboardButton("✏️ عنوان", callback_data=f"edittitle:{tid}"),
        ],
    ]
    detail_row = [InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{tid}")]
    if has_extra:
        detail_row.insert(
            0,
            InlineKeyboardButton(
                "بستن جزئیات" if expanded else "جزئیات",
                callback_data=f"details:{tid}",
            ),
        )
    rows.append(detail_row)
    return InlineKeyboardMarkup(rows)


def confirmed_text(txn: dict[str, Any]) -> str:
    title = txn.get("title") or ""
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
    return f"✅ ثبت شد\n{TITLE_EMOJI} {title}\n{AMOUNT_EMOJI} {amount}"
