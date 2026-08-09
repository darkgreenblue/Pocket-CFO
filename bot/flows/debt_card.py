"""رندر کارت بدهی/طلب — سومین نوع کارت، کنار کارتِ تراکنش و کارتِ هدف.

مثل بقیه‌ی کارت‌ها: رکوردِ ناقص خطِ «نیازمند تکمیل» می‌گیرد و با همان دکمه‌ها کامل می‌شود.
"""
from __future__ import annotations

from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.debts import KIND_EMOJI, KIND_LABELS, is_complete, normalize_kind, remaining
from bot.utils.money import format_amount


def render_debt_card(debt: dict[str, Any], *, expanded: bool = False,
                     recorder_name: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    kind = normalize_kind(debt.get("kind"))
    party = (debt.get("counterparty") or "").strip() or "— (نامشخص)"
    currency = debt.get("currency_display", "toman")
    headline = "بدهی به" if kind == "debt" else "طلب از"

    lines = [f"{KIND_EMOJI[kind]} {headline} {party}",
             f"💰 {format_amount(debt.get('amount'), currency)}"]

    settled = debt.get("settled_amount") or 0
    rest = remaining(debt)
    if debt.get("status") == "settled":
        lines.append("✅ تسویه شده")
    elif settled:
        lines.append(f"🧾 تسویه‌شده: {format_amount(settled, currency)}")
        lines.append(f"⏳ مانده: {format_amount(rest, currency)}")

    if debt.get("due_text"):
        lines.append(f"📅 سررسید: {debt['due_text']}")

    if expanded:
        if debt.get("title"):
            lines.append(f"📝 {debt['title']}")
        if debt.get("note"):
            lines.append(f"🗒 {debt['note']}")

    if recorder_name:
        lines.append(f"👤 ثبت‌کننده: {recorder_name}")

    if not is_complete(debt):
        missing = "طرف حساب و مبلغ" if (not (debt.get("counterparty") or "").strip()
                                        and debt.get("amount") is None) \
            else ("طرف حساب" if not (debt.get("counterparty") or "").strip() else "مبلغ")
        lines.append(f"⚠️ نیازمند تکمیل: {missing}")

    return "\n".join(lines), _keyboard(debt, expanded=expanded)


def _keyboard(debt: dict[str, Any], *, expanded: bool) -> InlineKeyboardMarkup:
    did = debt["id"]
    rows = [[
        InlineKeyboardButton("✏️ مبلغ", callback_data=f"debtamt:{did}"),
        InlineKeyboardButton("✏️ طرف حساب", callback_data=f"debtparty:{did}"),
    ]]
    last: list[InlineKeyboardButton] = []
    if debt.get("title") or debt.get("note"):
        last.append(InlineKeyboardButton(
            "بستن جزئیات" if expanded else "جزئیات", callback_data=f"debtdetails:{did}"))
    if debt.get("status") == "settled":
        last.append(InlineKeyboardButton("↩️ برگردان", callback_data=f"debtreopen:{did}"))
    else:
        last.append(InlineKeyboardButton("✅ تسویه شد", callback_data=f"debtsettle:{did}"))
    last.append(InlineKeyboardButton("🗑 حذف", callback_data=f"debtdelete:{did}"))
    rows.append(last)
    return InlineKeyboardMarkup(rows)


def kind_label(debt: dict[str, Any]) -> str:
    return KIND_LABELS[normalize_kind(debt.get("kind"))]
