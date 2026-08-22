"""ویرایشِ تراکنش در خانوارِ مشترک.

دفتر مشترک است و دکمه‌های کارت از اول روی تراکنشِ هر عضوی کار می‌کردند، ولی مسیرِ
گفتگویی (ریپلای روی کارت / گفتنِ اصلاح) هنوز `user_id` را چک می‌کرد. نتیجه بدترین
حالتِ ممکن بود: بی‌صدا شکست می‌خورد — نه خطایی، نه رفرشی — و شبیهِ «ربات نفهمید» بود.
"""
from bot.services import household as hh
from bot.services import transactions as ts

OWNER, PARTNER, OUTSIDER = 1, 2, 3


def _shared_household(db):
    hh.touch(OWNER, "علی")
    token = hh.create_invite(OWNER, "partner", True)
    hh.accept_invite(token, PARTNER, "مریم")


def test_partner_can_edit_a_transaction_recorded_by_the_other(db):
    _shared_household(db)
    txn_id = ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})

    assert ts.apply_update(PARTNER, txn_id, {"amount": 250000}) == txn_id
    assert db.get_transaction(txn_id)["amount"] == 250000


def test_editing_keeps_the_original_recorder(db):
    """ویرایشِ پارتنر نباید ثبت‌کننده را عوض کند؛ گزارشِ تفکیکی به آن تکیه دارد."""
    _shared_household(db)
    txn_id = ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})

    ts.apply_update(PARTNER, txn_id, {"title": "نانوایی"})
    txn = db.get_transaction(txn_id)
    assert txn["title"] == "نانوایی"
    assert txn["user_id"] == OWNER


def test_someone_outside_the_household_cannot_edit(db):
    _shared_household(db)
    txn_id = ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})

    assert ts.apply_update(OUTSIDER, txn_id, {"amount": 999}) is None
    assert db.get_transaction(txn_id)["amount"] == 200000


def test_solo_user_still_edits_their_own(db):
    txn_id = ts.create_from_item(OWNER, {"title": "نان", "amount": 200000})
    assert ts.apply_update(OWNER, txn_id, {"amount": 210000}) == txn_id
    assert db.get_transaction(txn_id)["amount"] == 210000


def test_missing_transaction_is_refused(db):
    assert ts.apply_update(OWNER, 4242, {"amount": 1000}) is None
