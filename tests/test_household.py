"""خانوار: دعوت، پیوستن، دفترِ مشترک، هدفِ تجمعی و هشدار برای هر دو عضو."""
import asyncio

import pytest

from bot.services import goals, household as hh
from bot.services import transactions as ts
from bot.services.reports import build_report

OWNER, PARTNER, THIRD = 1, 2, 3   # در conftest فقط کاربر ۱ در ALLOWED_USER_IDS است


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def _join(owner: int, member: int, name: str = "پارتنر", can_set_goals: bool = True) -> str:
    token = hh.create_invite(owner, "partner", can_set_goals)
    hh.accept_invite(token, member, name)
    return token


def test_solo_user_gets_own_household(db):
    hh.touch(OWNER, "علی")
    assert hh.hid(OWNER) is not None
    assert hh.is_shared(OWNER) is False
    assert hh.can_set_goals(OWNER) is True


def test_invite_grants_access_and_shares_ledger(db):
    hh.touch(OWNER, "علی")
    assert hh.authorized(PARTNER) is False       # هنوز عضو نیست و در تنظیمات هم نیست

    _join(OWNER, PARTNER, "مریم")

    assert hh.authorized(PARTNER) is True        # دسترسی از طریق مالکِ خانوار
    assert hh.hid(PARTNER) == hh.hid(OWNER)
    assert hh.is_shared(OWNER) is True
    assert hh.name_map(OWNER)[PARTNER] == "مریم"


def test_invite_is_single_use(db):
    hh.touch(OWNER, "علی")
    token = hh.create_invite(OWNER, "partner", True)
    hh.accept_invite(token, PARTNER, "مریم")
    with pytest.raises(hh.JoinError):
        hh.accept_invite(token, THIRD, "سارا")


def test_inviter_cannot_use_own_link(db):
    hh.touch(OWNER, "علی")
    token = hh.create_invite(OWNER, "partner", True)
    with pytest.raises(hh.JoinError):
        hh.accept_invite(token, OWNER, "علی")


def test_unknown_token_rejected(db):
    hh.touch(OWNER, "علی")
    with pytest.raises(hh.JoinError):
        hh.accept_invite("no-such-token", PARTNER, "مریم")


def test_transactions_of_both_members_land_in_one_ledger(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم")

    ts.create_from_item(OWNER, {"title": "نان", "amount": 100000})
    ts.create_from_item(PARTNER, {"title": "شیر", "amount": 200000})

    # هر دو عضو همان دفتر را می‌بینند…
    assert len(db.list_user_transactions(OWNER)) == 2
    assert len(db.list_user_transactions(PARTNER)) == 2
    # …ولی ثبت‌کننده‌ی هر مورد جدا نگه داشته می‌شود.
    recorders = {t["title"]: t["user_id"] for t in db.list_user_transactions(OWNER)}
    assert recorders == {"نان": OWNER, "شیر": PARTNER}


def test_joining_brings_previous_data_into_shared_ledger(db):
    hh.touch(OWNER, "علی")
    ts.create_from_item(PARTNER, {"title": "خرجِ قبل از پیوستن", "amount": 50000})
    _join(OWNER, PARTNER, "مریم")

    titles = {t["title"] for t in db.list_user_transactions(OWNER)}
    assert "خرجِ قبل از پیوستن" in titles


def test_goal_is_shared_and_alerts_reach_both_members(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم")

    goals.create_or_update_from_item(OWNER, {"topic": "رستوران", "limit_amount": 1000000})
    # خرجِ پارتنر هم به همان لیمیتِ مشترک می‌خورد.
    ts.create_from_item(PARTNER, {"title": "رستوران", "amount": 600000,
                                  "suggested_tags": ["رستوران"]})

    bot = _FakeBot()
    asyncio.run(goals.evaluate_and_alert(bot, PARTNER))

    assert {chat_id for chat_id, _ in bot.sent} == {OWNER, PARTNER}
    assert all(text.startswith("🟡") for _, text in bot.sent)


def test_goal_not_duplicated_when_partner_sets_same_topic(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم")

    first = goals.create_or_update_from_item(OWNER, {"topic": "رستوران", "limit_amount": 1000000})
    second = goals.create_or_update_from_item(PARTNER, {"topic": "رستوران",
                                                        "limit_amount": 1500000})
    assert first == second
    assert db.get_goal(first)["limit_amount"] == 1500000


def test_member_without_permission_cannot_set_goals(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم", can_set_goals=False)

    assert hh.can_set_goals(PARTNER) is False
    assert goals.create_or_update_from_item(PARTNER, {"topic": "کافه",
                                                      "limit_amount": 500000}) is None
    # ولی صاحبِ خانوار همچنان می‌تواند.
    assert goals.create_or_update_from_item(OWNER, {"topic": "کافه",
                                                    "limit_amount": 500000}) is not None


def test_member_without_permission_cannot_edit_shared_goal(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم", can_set_goals=False)
    gid = goals.create_or_update_from_item(OWNER, {"topic": "رستوران", "limit_amount": 1000000})

    assert goals.apply_update(PARTNER, gid, {"limit_amount": 9000000}) is None
    assert db.get_goal(gid)["limit_amount"] == 1000000


def test_report_breaks_down_by_recorder(db):
    hh.touch(OWNER, "علی")
    _join(OWNER, PARTNER, "مریم")
    ts.create_from_item(OWNER, {"title": "نان", "amount": 100000})
    ts.create_from_item(PARTNER, {"title": "شیر", "amount": 200000})

    report = build_report(OWNER, period="today")
    assert "به تفکیک ثبت‌کننده" in report
    assert "علی" in report and "مریم" in report
