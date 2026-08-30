"""کیبوردهای ثابت (منوی پایین، انتخاب بازه‌ی گزارش، و ویزاردِ افزودن عضو به خانوار)."""
from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.services.household import RELATIONS

BTN_ADD = "➕ افزودن خرید"
BTN_DEBT = "🤝 ثبت بدهی/طلب"
BTN_GOAL = "🎯 ثبت هدف"
BTN_REPORT = "📊 گزارش"
BTN_HOUSEHOLD = "👨‍👩‍👧 افزودن عضو به خانوار"


def main_menu() -> ReplyKeyboardMarkup:
    """منوی پایین. `is_persistent` یعنی تلگرام جمعش نکند و همیشه در دسترس بماند —
    وگرنه کاربر بعد از یک‌بار بستن، تا `/start` بعدی دیگر دکمه‌ها را نمی‌بیند."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ADD), KeyboardButton(BTN_REPORT)],
            [KeyboardButton(BTN_DEBT), KeyboardButton(BTN_GOAL)],
            [KeyboardButton(BTN_HOUSEHOLD)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def clarify_keyboard(clar_id: int) -> InlineKeyboardMarkup:
    """«این خرج بود یا بدهی؟» — دو گزینه‌ی صریح به‌جای یک حدسِ اشتباه."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 خرج بود", callback_data=f"clr:{clar_id}:txn"),
        InlineKeyboardButton("🤝 بدهی/طلب", callback_data=f"clr:{clar_id}:debt"),
    ]])


def cancel_keyboard() -> InlineKeyboardMarkup:
    """تنها دکمه‌ی هر پرسشِ ویرایش: راهِ خروج بدون جواب‌دادن."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖️ انصراف", callback_data="editcancel:0")]]
    )


def report_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("امروز", callback_data="report:today"),
                InlineKeyboardButton("این هفته", callback_data="report:week"),
                InlineKeyboardButton("این ماه", callback_data="report:month"),
            ]
        ]
    )


# ---------- ویزارد دعوت به خانوار ----------

def relation_keyboard() -> InlineKeyboardMarkup:
    """گام ۱: نسبتِ فردی که دعوت می‌شود."""
    rows = [[InlineKeyboardButton(label, callback_data=f"hhrel:{code}")]
            for code, label in RELATIONS.items()]
    rows.append([InlineKeyboardButton("انصراف", callback_data="hhcancel:0")])
    return InlineKeyboardMarkup(rows)


def permission_keyboard() -> InlineKeyboardMarkup:
    """گام ۲: سطح دسترسی (فعلاً فقط اجازه‌ی هدف‌گذاری در خانوار)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اجازه‌ی هدف‌گذاری دارد", callback_data="hhperm:1")],
        [InlineKeyboardButton("🚫 فقط ثبت و مشاهده (بدون هدف‌گذاری)",
                              callback_data="hhperm:0")],
        [InlineKeyboardButton("انصراف", callback_data="hhcancel:0")],
    ])


# ---------- ریزِ تراکنش‌ها ----------

def report_detail_keyboard(period: str) -> InlineKeyboardMarkup:
    """زیرِ گزارشِ خلاصه: راهِ رسیدن به فهرستِ کاملِ همان بازه."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧾 ریز تراکنش‌ها", callback_data=f"rdetail:{period}")]]
    )


def detail_scope_keyboard(period: str, members: list[dict], me: int) -> InlineKeyboardMarkup:
    """انتخابِ اینکه ریزِ چه کسی را می‌خواهی: کل خانوار، خودت، یا هر عضو."""
    rows = [[InlineKeyboardButton("👨‍👩‍👧 کل خانوار", callback_data=f"rdet:{period}:all")]]
    for member in members:
        name = (member["display_name"] or "").strip() or "عضو خانوار"
        if member["user_id"] == me:
            name = f"{name} (تو)"
        rows.append([InlineKeyboardButton(
            f"👤 {name}", callback_data=f"rdet:{period}:{member['user_id']}")])
    return InlineKeyboardMarkup(rows)
