"""رندر کارت تأیید تراکنش و مدیریت state ویرایش.

State ویرایش در ``context.user_data["awaiting"]`` نگهداری می‌شود:
    {"action": "amount" | "title", "txn_id": <id>}
هر اکشن دکمه‌ای هم ``txn_id`` را در callback_data حمل می‌کند تا به کارت
درستش بایند شود و پیام‌های هم‌زمان دچار تداخل نشوند.
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.transactions import is_complete
from bot.utils.money import format_amount, to_persian_digits, unit_label, other_unit

AWAITING_KEY = "awaiting"
EXPANDED_KEY = "expanded_cards"


def render_card(txn: dict[str, Any], *, expanded: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    title = txn.get("title") or "— (تعیین‌نشده)"
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
    items = txn.get("mentioned_items") or []
    tags = txn.get("tags") or []

    lines = ["🧾 تراکنش جدید", f"عنوان: {title}", f"مبلغ: {amount}"]

    if items:
        if expanded:
            lines.append("🧺 اقلام:")
            lines += [f"   ▫️ {it}" for it in items]
        else:
            lines.append(f"🧺 اقلام: {to_persian_digits(len(items))} قلم")
    if tags:
        lines.append(f"🏷 تگ‌ها: {'، '.join(tags)}")
    if txn.get("note"):
        lines.append(f"📝 {txn['note']}")

    if not is_complete(txn):
        lines.append("")
        lines.append("⚠️ برای ثبت، مبلغ و عنوان باید کامل باشند.")

    text = "\n".join(lines)
    return text, _keyboard(txn, expanded=expanded, has_items=bool(items))


def _keyboard(txn: dict[str, Any], *, expanded: bool, has_items: bool) -> InlineKeyboardMarkup:
    tid = txn["id"]
    unit = txn.get("currency_display", "toman")
    rows = [
        [InlineKeyboardButton("✅ ثبت", callback_data=f"confirm:{tid}")],
        [
            InlineKeyboardButton("✏️ مبلغ", callback_data=f"editamt:{tid}"),
            InlineKeyboardButton("✏️ عنوان", callback_data=f"edittitle:{tid}"),
        ],
        [
            InlineKeyboardButton(
                f"🔁 به {unit_label(other_unit(unit))}", callback_data=f"unit:{tid}"
            ),
        ],
    ]
    if has_items:
        rows[-1].append(
            InlineKeyboardButton(
                "🧺 بستن جزئیات" if expanded else "🧺 جزئیات",
                callback_data=f"details:{tid}",
            )
        )
    rows.append([InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{tid}")])
    return InlineKeyboardMarkup(rows)


def confirmed_text(txn: dict[str, Any]) -> str:
    title = txn.get("title") or ""
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
    tags = txn.get("tags") or []
    line = f"✅ ثبت شد: {title} — {amount}"
    if tags:
        line += f"\n🏷 {'، '.join(tags)}"
    return line
