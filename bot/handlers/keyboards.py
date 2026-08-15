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
BTN_REPORT = "📊 گزارش"
BTN_HOUSEHOLD = "👨‍👩‍👧 افزودن عضو به خانوار"
BTN_RESET = "♻️ ریست تست"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ADD), KeyboardButton(BTN_REPORT)],
            [KeyboardButton(BTN_HOUSEHOLD)],
            [KeyboardButton(BTN_RESET)],
        ],
        resize_keyboard=True,
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
