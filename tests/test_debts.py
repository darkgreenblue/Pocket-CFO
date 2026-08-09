"""بدهی و طلب: تشخیص نوع، کامل/ناقص، تسویه‌ی کامل و جزئی، و جمع‌بندی."""
from bot.flows.debt_card import render_debt_card
from bot.services import debts
from bot.services import household as hh

USER, PARTNER = 1, 2


def _new(user_id, **item):
    debt_id, created = debts.create_or_update_from_item(user_id, item)
    return debt_id, created


def test_kind_detection_defaults_to_debt(db):
    assert debts.normalize_kind("credit") == debts.CREDIT
    assert debts.normalize_kind("طلب") == debts.CREDIT
    assert debts.normalize_kind("debt") == debts.DEBT
    assert debts.normalize_kind(None) == debts.DEBT
    assert debts.normalize_kind("چیزِ نامعلوم") == debts.DEBT


def test_create_debt_is_open_and_titled(db):
    debt_id, created = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    assert created is True
    debt = db.get_debt(debt_id)
    assert debt["status"] == "open"
    assert debt["kind"] == "debt"
    assert debt["title"] == "بدهی رضا"
    assert debts.remaining(debt) == 500000


def test_incomplete_debt_stays_draft(db):
    debt_id, _ = _new(USER, kind="credit", counterparty="مریم")   # بدون مبلغ
    debt = db.get_debt(debt_id)
    assert debt["status"] == "draft"
    assert debts.is_complete(debt) is False

    debts.apply_update(USER, debt_id, {"amount": 300000})
    assert db.get_debt(debt_id)["status"] == "open"


def test_settle_button_closes_the_debt(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    debts.settle(USER, debt_id)
    debt = db.get_debt(debt_id)
    assert debt["status"] == "settled"
    assert debts.remaining(debt) == 0
    assert debt["settled_at"]


def test_partial_settlement_keeps_it_open(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    debts.apply_update(USER, debt_id, {"settle_amount": 200000})
    debt = db.get_debt(debt_id)
    assert debt["status"] == "open"
    assert debts.remaining(debt) == 300000


def test_settlement_reuses_existing_open_debt(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    # کاربر بعداً می‌گوید «بدهیم به رضا رو دادم» — نباید رکوردِ دوم ساخته شود.
    same_id, created = _new(USER, kind="debt", counterparty="رضا", settled=True)
    assert (same_id, created) == (debt_id, False)
    assert db.get_debt(debt_id)["status"] == "settled"
    assert len(db.list_debts(USER, include_settled=True)) == 1


def test_reopen_undoes_settlement(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    debts.settle(USER, debt_id)
    debts.reopen(USER, debt_id)
    assert db.get_debt(debt_id)["status"] == "open"
    assert debts.remaining(db.get_debt(debt_id)) == 500000


def test_totals_and_net(db):
    _new(USER, kind="debt", counterparty="رضا", amount=500000)
    _new(USER, kind="credit", counterparty="مریم", amount=800000)
    _new(USER, kind="credit", counterparty="سعید", amount=100, currency="usd")

    totals = debts.totals(USER)
    assert totals[debts.DEBT] == {"toman": 500000}
    assert totals[debts.CREDIT] == {"toman": 800000, "usd": 100}
    # ارز خارجی وارد خالصِ تومانی نمی‌شود.
    assert debts.net_toman(USER) == 300000


def test_settled_debts_hidden_from_open_list(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا", amount=500000)
    debts.settle(USER, debt_id)
    assert db.list_debts(USER) == []
    assert len(db.list_debts(USER, include_settled=True)) == 1


def test_debts_are_shared_across_household(db):
    hh.touch(USER, "علی")
    token = hh.create_invite(USER, "partner", True)
    hh.accept_invite(token, PARTNER, "مریم")

    _new(USER, kind="debt", counterparty="رضا", amount=500000)
    _new(PARTNER, kind="credit", counterparty="سعید", amount=200000)

    assert len(db.list_debts(PARTNER)) == 2
    # پارتنر می‌تواند بدهیِ ثبت‌شده توسط دیگری را تسویه کند (دفتر مشترک است).
    shared = db.list_debts(PARTNER, kind="debt")[0]
    assert debts.apply_update(PARTNER, shared["id"], {"settled": True}) == shared["id"]
    assert db.get_debt(shared["id"])["status"] == "settled"


def test_card_shows_direction_remaining_and_recorder(db):
    debt_id, _ = _new(USER, kind="credit", counterparty="مریم", amount=800000,
                      due_text="آخر ماه")
    debts.apply_update(USER, debt_id, {"settle_amount": 300000})
    text, keyboard = render_debt_card(db.get_debt(debt_id), recorder_name="علی")

    assert "طلب از مریم" in text
    assert "سررسید: آخر ماه" in text
    assert "ثبت‌کننده: علی" in text
    assert "مانده" in text
    buttons = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"debtsettle:{debt_id}" in buttons


def test_incomplete_card_asks_for_missing_fields(db):
    debt_id, _ = _new(USER, kind="debt", counterparty="رضا")
    text, _ = render_debt_card(db.get_debt(debt_id))
    assert "نیازمند تکمیل: مبلغ" in text
