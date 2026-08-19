"""گزارش باید ارز خارجی را **نشان بدهد**، نه اینکه بی‌صدا حذفش کند.

باگِ واقعی: `to_rial` برای هر واحدی جز تومان/ریال صفر برمی‌گرداند، پس یک خرجِ دلاری
نه در مجموع می‌آمد، نه در تفکیک دسته — عملاً انگار اصلاً ثبت نشده بود.
"""
from bot.services import household as hh
from bot.services import transactions as ts
from bot.services.reports import build_report

USER, PARTNER = 1, 2


def test_dollar_expense_appears_in_report(db):
    ts.create_from_item(USER, {"title": "اشتراک", "amount": 1.5, "currency": "usd"})
    report = build_report(USER, period="today")

    assert "دلار" in report
    assert "۱٫۵۰" in report


def test_report_with_only_foreign_currency_is_not_empty(db):
    ts.create_from_item(USER, {"title": "سرور", "amount": 12, "currency": "usd"})
    report = build_report(USER, period="today")

    assert "هیچ تراکنش" not in report
    assert "دلار" in report
    # جمعِ «۰ تومان» گمراه‌کننده است وقتی هیچ خرجِ تومانی نبوده.
    assert "مجموع خرج: ۰ تومان" not in report


def test_toman_and_foreign_are_reported_separately(db):
    ts.create_from_item(USER, {"title": "نان", "amount": 200000})
    ts.create_from_item(USER, {"title": "اشتراک", "amount": 1.5, "currency": "usd"})
    report = build_report(USER, period="today")

    assert "مجموع خرج: ۲۰۰٬۰۰۰ تومان" in report   # ارز خارجی قاتیِ تومان نشده
    assert "دلار" in report
    assert "تعداد تراکنش: ۲" in report


def test_multiple_foreign_currencies_stay_separate(db):
    ts.create_from_item(USER, {"title": "الف", "amount": 10, "currency": "usd"})
    ts.create_from_item(USER, {"title": "ب", "amount": 50, "currency": "aed"})
    report = build_report(USER, period="today")

    assert "دلار" in report and "درهم" in report


def test_member_with_only_foreign_expenses_is_still_listed(db):
    hh.touch(USER, "علی")
    token = hh.create_invite(USER, "partner", True)
    hh.accept_invite(token, PARTNER, "مریم")

    ts.create_from_item(USER, {"title": "نان", "amount": 200000})
    ts.create_from_item(PARTNER, {"title": "اشتراک", "amount": 1.5, "currency": "usd"})
    report = build_report(USER, period="today")

    assert "مریم" in report                    # قبلاً «۰ تومان» می‌شد یا گم می‌شد
    assert "فقط ارز خارجی" in report
