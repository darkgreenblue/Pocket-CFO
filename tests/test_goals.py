import asyncio

from bot.services import goals, transactions as ts
from bot.utils import jalali


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)


def _add(topic_tag_amount, db):
    return ts.create_from_item(1, {"title": "رستوران", "amount": topic_tag_amount,
                                   "suggested_tags": ["رستوران"]})


def test_goal_create_and_upsert(db):
    gid = goals.create_or_update_from_item(1, {"topic": "رستوران", "limit_amount": 3000000})
    g = db.get_goal(gid)
    assert g["status"] == "active" and g["tag_id"] is not None
    gid2 = goals.create_or_update_from_item(1, {"topic": "رستوران", "limit_amount": 3500000})
    assert gid2 == gid
    assert db.get_goal(gid)["limit_amount"] == 3500000


def test_goal_incomplete_needs_limit(db):
    gid = goals.create_or_update_from_item(1, {"topic": "کافه"})
    assert db.get_goal(gid)["status"] == "draft"


def test_alarm_only_highest_threshold(db):
    goals.create_or_update_from_item(1, {"topic": "رستوران", "limit_amount": 1000000})
    bot = _FakeBot()
    _add(400000, db)                       # 40%
    asyncio.run(goals.evaluate_and_alert(bot, 1))
    assert bot.sent == []
    _add(200000, db)                       # 60% → آلارم ۵۰
    asyncio.run(goals.evaluate_and_alert(bot, 1))
    assert len(bot.sent) == 1 and bot.sent[0].startswith("🟡")
    _add(600000, db)                       # 120% → مستقیم آلارم ۱۰۰ (نه ۸۰)
    asyncio.run(goals.evaluate_and_alert(bot, 1))
    assert len(bot.sent) == 2 and bot.sent[1].startswith("🔴")


def test_past_month_transaction(db):
    tid = ts.create_from_item(1, {"title": "خرج قدیمی", "amount": 100000, "past_month": "خرداد"})
    assert db.get_transaction(tid)["jmonth"] == 3
