"""اعشار در مبلغ — باگِ واقعی: ویرایشِ «۱.۴ دلار» به ۱۴ دلار تبدیل می‌شد.

`parse_amount` قبلاً همه‌ی غیررقم‌ها را دور می‌ریخت، پس ممیز بی‌صدا حذف می‌شد و مبلغ
ده‌برابر ثبت می‌شد — بدترین نوعِ باگ، چون هیچ خطایی نمی‌داد.
"""
import pytest

from bot.utils.money import format_amount, parse_amount


@pytest.mark.parametrize("text, expected", [
    ("1.4", 1.4),
    ("۱.۴", 1.4),
    ("۱٫۴", 1.4),          # ممیزِ فارسی
    ("۱/۴", 1.4),          # ممیز روی کیبورد فارسی
    ("12.75", 12.75),
    ("0.5", 0.5),
    ("1.4 دلار", 1.4),     # واحد کنارِ عدد
])
def test_real_decimals_survive(text, expected):
    assert parse_amount(text) == pytest.approx(expected)


@pytest.mark.parametrize("text, expected", [
    ("250000", 250000),
    ("۲۵۰۰۰۰", 250000),
    ("250,000", 250000),
    ("۲۵۰٬۰۰۰", 250000),
    ("1.500", 1500),        # سه رقم بعد از نقطه = هزارگان، نه اعشار
    ("250.000", 250000),
    ("1.500.000", 1500000),  # چند جداکننده = قطعاً هزارگان
])
def test_thousands_separators_are_not_decimals(text, expected):
    assert parse_amount(text) == expected


def test_integral_decimal_collapses_to_int():
    """«۲.۰» عملاً عدد صحیح است؛ float نگهش نمی‌داریم."""
    value = parse_amount("2.0")
    assert value == 2 and isinstance(value, int)


def test_no_digits_returns_none():
    assert parse_amount("") is None
    assert parse_amount("سلام") is None
    assert parse_amount(".") is None


def test_parsed_decimal_formats_back_correctly():
    """چیزی که parse می‌شود باید روی کارت هم درست دیده شود."""
    assert format_amount(parse_amount("1.4"), "usd") == "۱٫۴۰ دلار"
    assert format_amount(parse_amount("250,000"), "toman") == "۲۵۰٬۰۰۰ تومان"
