from bot.utils.money import (
    format_amount,
    group_digits,
    other_unit,
    parse_amount,
    to_rial,
)


def test_parse_amount_latin():
    assert parse_amount("250000") == 250000


def test_parse_amount_persian_digits():
    assert parse_amount("۲۵۰۰۰۰") == 250000


def test_parse_amount_with_separators_and_words():
    assert parse_amount("۲۵۰٬۰۰۰ تومان") == 250000
    assert parse_amount("1,500,000") == 1500000


def test_parse_amount_no_digits():
    assert parse_amount("سلام") is None
    assert parse_amount("") is None


def test_group_digits_persian():
    assert group_digits(250000) == "۲۵۰٬۰۰۰"


def test_format_amount_units():
    assert format_amount(250000, "toman") == "۲۵۰٬۰۰۰ تومان"
    assert format_amount(250000, "rial") == "۲۵۰٬۰۰۰ ریال"
    assert format_amount(None, "toman") == "— (تعیین‌نشده)"


def test_to_rial_conversion():
    assert to_rial(1000, "toman") == 10000
    assert to_rial(1000, "rial") == 1000
    assert to_rial(None, "toman") == 0


def test_other_unit():
    assert other_unit("toman") == "rial"
    assert other_unit("rial") == "toman"
