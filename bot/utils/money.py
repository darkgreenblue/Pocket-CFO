"""کار با مبلغ: نرمال‌سازی ارقام، parse، فرمت با جداکننده و واحد.

نکته‌ی واحد: عددی که کاربر تایپ/می‌گوید یک عدد خام است و واحدش (تومان/ریال)
جداگانه نگهداری می‌شود. دکمه‌ی تغییر واحد فقط برچسب را عوض می‌کند تا اگر ربات
واحد را اشتباه حدس زد، کاربر بتواند اصلاحش کند. برای گزارش‌ها همه چیز با
``to_rial`` به ریال تبدیل و جمع می‌شود.
"""
from __future__ import annotations

from typing import Optional

# نگاشت ارقام فارسی/عربی به لاتین
_DIGIT_MAP = {
    ord("۰"): "0", ord("۱"): "1", ord("۲"): "2", ord("۳"): "3", ord("۴"): "4",
    ord("۵"): "5", ord("۶"): "6", ord("۷"): "7", ord("۸"): "8", ord("۹"): "9",
    ord("٠"): "0", ord("١"): "1", ord("٢"): "2", ord("٣"): "3", ord("٤"): "4",
    ord("٥"): "5", ord("٦"): "6", ord("٧"): "7", ord("٨"): "8", ord("٩"): "9",
}

_LATIN_TO_PERSIAN = {ord(str(d)): "۰۱۲۳۴۵۶۷۸۹"[d] for d in range(10)}

UNIT_LABELS = {"toman": "تومان", "rial": "ریال"}


def normalize_digits(text: str) -> str:
    """ارقام فارسی/عربی را به لاتین تبدیل می‌کند."""
    return (text or "").translate(_DIGIT_MAP)


def to_persian_digits(text: str) -> str:
    return str(text).translate(_LATIN_TO_PERSIAN)


def parse_amount(text: str) -> Optional[int]:
    """از یک رشته‌ی ورودی کاربر، یک عدد صحیح بیرون می‌کشد.

    جداکننده‌ها، فاصله و کاراکترهای غیرعددی نادیده گرفته می‌شوند.
    اگر هیچ رقمی نباشد ``None`` برمی‌گرداند.
    """
    if not text:
        return None
    digits = "".join(ch for ch in normalize_digits(text) if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def group_digits(value: int) -> str:
    """عدد را با جداکننده‌ی هزارگان فارسی (٬) و ارقام فارسی برمی‌گرداند."""
    grouped = f"{int(value):,}".replace(",", "٬")
    return to_persian_digits(grouped)


def unit_label(unit: str) -> str:
    return UNIT_LABELS.get(unit, UNIT_LABELS["toman"])


def format_amount(value: Optional[int], unit: str = "toman") -> str:
    """مبلغ را به صورت «۲۵۰٬۰۰۰ تومان» برمی‌گرداند."""
    if value is None:
        return "— (تعیین‌نشده)"
    return f"{group_digits(value)} {unit_label(unit)}"


def to_rial(value: Optional[int], unit: str) -> int:
    """مبلغ را برای جمع‌بندی گزارش‌ها به ریال تبدیل می‌کند."""
    if value is None:
        return 0
    return int(value) * 10 if unit == "toman" else int(value)


def other_unit(unit: str) -> str:
    return "rial" if unit == "toman" else "toman"
