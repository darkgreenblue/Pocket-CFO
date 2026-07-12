"""کمک‌تابع‌های تقویم شمسی (جلالی) — ربات باید همیشه بداند امروز کدام روز از کدام ماه است."""
from __future__ import annotations

from typing import Optional

import jdatetime

from bot.utils.money import to_persian_digits

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

# نام‌ها و مترادف‌های رایج برای تشخیص ماه از متن
_MONTH_ALIASES = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
    "ابان": 8, "اذر": 9,
}


def today() -> jdatetime.date:
    return jdatetime.date.today()


def current_ym() -> tuple[int, int]:
    t = today()
    return t.year, t.month


def month_name(jmonth: int) -> str:
    return MONTHS[(jmonth - 1) % 12]


def is_leap(jyear: int) -> bool:
    try:
        return jdatetime.date(jyear, 12, 30).isvalid() if hasattr(jdatetime.date, "isvalid") \
            else _leap_by_try(jyear)
    except Exception:  # noqa: BLE001
        return _leap_by_try(jyear)


def _leap_by_try(jyear: int) -> bool:
    try:
        jdatetime.date(jyear, 12, 30)
        return True
    except ValueError:
        return False


def month_last_day(jyear: int, jmonth: int) -> int:
    if jmonth <= 6:
        return 31
    if jmonth <= 11:
        return 30
    return 30 if _leap_by_try(jyear) else 29


def today_str() -> str:
    t = today()
    return f"{to_persian_digits(t.day)} {month_name(t.month)} {to_persian_digits(t.year)}"


def month_end_str(jyear: int, jmonth: int) -> str:
    return f"{to_persian_digits(month_last_day(jyear, jmonth))} {month_name(jmonth)} " \
           f"{to_persian_digits(jyear)}"


def month_number(name: str) -> Optional[int]:
    """شماره‌ی ماه را از نام (با نرمال‌سازی ساده) برمی‌گرداند."""
    if not name:
        return None
    s = name.strip().replace("ك", "ک").replace("ي", "ی").replace("‌", " ")
    s = " ".join(s.split())
    for key, num in _MONTH_ALIASES.items():
        if key in s:
            return num
    return None


def resolve_past_month(name: str) -> Optional[tuple[int, int]]:
    """از نام ماه، (سال، ماه) شمسی را استنتاج می‌کند.

    اگر ماهِ گفته‌شده ≤ ماهِ جاری باشد → همین سال؛ وگرنه سالِ قبل (اشاره به گذشته).
    """
    m = month_number(name)
    if m is None:
        return None
    cy, cm = current_ym()
    return (cy if m <= cm else cy - 1), m
