"""ریزِ تراکنش‌ها: گزارشِ کامل، نه خلاصه — با امکانِ تفکیکِ عضو."""
from bot.handlers.keyboards import detail_scope_keyboard, main_menu, report_detail_keyboard
from bot.services import household as hh
from bot.services import reports
from bot.services import transactions as ts

OWNER, PARTNER = 1, 2


def _shared():
    hh.touch(OWNER, "علی")
    token = hh.create_invite(OWNER, "partner", True)
    hh.accept_invite(token, PARTNER, "مریم")


def test_detail_lists_every_field_of_each_transaction(db):
    ts.create_from_item(OWNER, {"title": "سوپرمارکت", "amount": 450000,
                                "suggested_tags": ["سوپرمارکت"],
                                "mentioned_items": ["نان", "شیر"],
                                "note": "خرید هفتگی"},
                        transcript="چهارصد و پنجاه تومن سوپرمارکت")
    detail = reports.build_detail(OWNER, "today", reports.SCOPE_ALL)

    for expected in ("سوپرمارکت", "۴۵۰٬۰۰۰", "نان، شیر", "خرید هفتگی",
                     "چهارصد و پنجاه تومن"):
        assert expected in detail, expected


def test_items_are_not_printed_as_raw_json(db):
    """`confirmed_in_range` قبلاً JSON را باز نمی‌کرد و اقلام خام چاپ می‌شدند."""
    ts.create_from_item(OWNER, {"title": "خرید", "amount": 1000,
                                "mentioned_items": ["نان", "شیر"]})
    detail = reports.build_detail(OWNER, "today")
    assert '["' not in detail and "\\u" not in detail


def test_scope_filters_to_one_member(db):
    _shared()
    ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})
    ts.create_from_item(PARTNER, {"title": "رستوران", "amount": 800000})

    everyone = reports.build_detail(OWNER, "today", reports.SCOPE_ALL)
    assert "نان" in everyone and "رستوران" in everyone

    just_partner = reports.build_detail(OWNER, "today", str(PARTNER))
    assert "رستوران" in just_partner and "نان" not in just_partner
    assert "مریم" in just_partner            # عنوانِ گزارش می‌گوید مالِ کیست

    just_me = reports.build_detail(OWNER, "today", str(OWNER))
    assert "نان" in just_me and "رستوران" not in just_me


def test_detail_totals_keep_currencies_apart(db):
    ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})
    ts.create_from_item(OWNER, {"title": "اشتراک", "amount": 1.4, "currency": "usd"})
    detail = reports.build_detail(OWNER, "today")

    assert "جمع: ۲۰۰٬۰۰۰ تومان" in detail
    assert "دلار" in detail


def test_empty_scope_says_so_instead_of_breaking(db):
    _shared()
    ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})
    assert "چیزی ثبت نشده" in reports.build_detail(OWNER, "today", str(PARTNER))


def test_long_detail_exceeds_message_limit_so_it_goes_to_a_file(db):
    """سیگنالی که هندلر با آن تصمیم می‌گیرد فایل بفرستد."""
    for i in range(60):
        ts.create_from_item(OWNER, {"title": f"خرید شماره {i}", "amount": 100000 + i,
                                    "note": "توضیح نسبتاً بلند برای این تراکنش"})
    detail = reports.build_detail(OWNER, "today")
    assert len(detail) > reports.TELEGRAM_SAFE_CHARS


def test_scope_chooser_offers_household_and_each_member(db):
    _shared()
    keyboard = detail_scope_keyboard("today", hh.members(OWNER), OWNER)
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    labels = " ".join(b.text for row in keyboard.inline_keyboard for b in row)

    assert "rdet:today:all" in data
    assert f"rdet:today:{OWNER}" in data and f"rdet:today:{PARTNER}" in data
    assert "(تو)" in labels                  # کدام یکی خودت هستی مشخص است


def test_summary_offers_the_itemised_view(db):
    data = [b.callback_data for row in report_detail_keyboard("week").inline_keyboard
            for b in row]
    assert data == ["rdetail:week"]


def test_menu_has_debt_and_goal_buttons(db):
    labels = [b.text for row in main_menu().keyboard for b in row]
    assert any("بدهی" in label for label in labels), labels
    assert any("هدف" in label for label in labels), labels
