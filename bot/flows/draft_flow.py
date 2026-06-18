"""رندر کارت تراکنش و مدیریت state ویرایش.

ثبت خودکار است: تراکنشِ کامل (مبلغ+عنوان) همان لحظه ثبت می‌شود و کارت فقط دکمه‌های
ویرایش/حذف دارد. تراکنش ناقص، خط هشدار «نیازمند تکمیل» می‌گیرد تا کاربر کاملش کند.

State ویرایشِ دکمه‌ای در ``context.user_data["awaiting"]`` = {"action","txn_id"}.
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.transactions import is_complete
from bot.utils.money import format_amount

AWAITING_KEY = "awaiting"
EXPANDED_KEY = "expanded_cards"

TITLE_EMOJI = "📝"
AMOUNT_EMOJI = "💰"


def render_card(txn: dict[str, Any], *, expanded: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    title = (txn.get("title") or "").strip() or "— (نامشخص)"
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
        missing = "عنوان و مبلغ" if (not txn.get("title") and txn.get("amount") is None) else \
            ("عنوان" if not txn.get("title") else "مبلغ")
        lines.append(f"⚠️ نیازمند تکمیل: {missing}")

    return "\n".join(lines), _keyboard(txn, expanded=expanded)


def _keyboard(txn: dict[str, Any], *, expanded: bool) -> InlineKeyboardMarkup:
    tid = txn["id"]
    has_extra = bool(txn.get("mentioned_items") or txn.get("tags") or txn.get("note"))
    rows = [[
        InlineKeyboardButton("✏️ مبلغ", callback_data=f"editamt:{tid}"),
        InlineKeyboardButton("✏️ عنوان", callback_data=f"edittitle:{tid}"),
    ]]
    last = [InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{tid}")]
    if has_extra:
        last.insert(0, InlineKeyboardButton(
            "بستن جزئیات" if expanded else "جزئیات", callback_data=f"details:{tid}"))
    rows.append(last)
    return InlineKeyboardMarkup(rows)
