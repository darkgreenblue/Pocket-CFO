"""رندر کارت هدف مالی (بودجه‌ی ماهانه). مشابه کارت تراکنش، بدون دکمه‌ی تأیید."""
from __future__ import annotations

from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.goals import is_complete
from bot.utils import jalali
from bot.utils.money import group_digits


def render_goal_card(goal: dict[str, Any], *, expanded: bool = False,
                     recorder_name: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    topic = (goal.get("topic") or "").strip() or "— (نامشخص)"
    limit = goal.get("limit_amount")
    limit_str = f"{group_digits(limit)} تومان" if limit is not None else "— (تعیین‌نشده)"
    month = jalali.month_name(goal["jmonth"])

    lines = ["🎯 هدف بودجه", f"📌 {topic}", f"💰 سقف {month}: {limit_str}"]
    if expanded and goal.get("note"):
        lines.append("🗒 " + goal["note"])
    if recorder_name:
        lines.append(f"👤 ثبت‌کننده: {recorder_name}")
    if not is_complete(goal):
        missing = "موضوع و سقف" if (not goal.get("topic") and goal.get("limit_amount") is None) \
            else ("موضوع" if not goal.get("topic") else "سقف")
        lines.append(f"⚠️ نیازمند تکمیل: {missing}")

    return "\n".join(lines), _keyboard(goal, expanded=expanded)


def _keyboard(goal: dict[str, Any], *, expanded: bool) -> InlineKeyboardMarkup:
    gid = goal["id"]
    rows = [[
        InlineKeyboardButton("✏️ سقف", callback_data=f"goaleditlimit:{gid}"),
        InlineKeyboardButton("✏️ موضوع", callback_data=f"goaledittopic:{gid}"),
    ]]
    last = [InlineKeyboardButton("🗑 حذف", callback_data=f"goaldelete:{gid}")]
    if goal.get("note"):
        last.insert(0, InlineKeyboardButton(
            "بستن جزئیات" if expanded else "جزئیات", callback_data=f"goaldetails:{gid}"))
    rows.append(last)
    return InlineKeyboardMarkup(rows)
