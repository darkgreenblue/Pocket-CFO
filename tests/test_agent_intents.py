"""تشخیص نیت: یک پیام می‌تواند هم‌زمان تراکنش، بدهی/طلب و هدف بسازد."""
import asyncio
import json
from types import SimpleNamespace

from bot.llm import agent
from bot.services import household as hh

USER, PARTNER = 1, 2


def _fake_chat(payload: dict):
    async def _chat(messages, tools=None, json_mode=False):
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=None)
    return _chat


def _run(monkeypatch, payload, user_id=USER, text="یک پیام"):
    monkeypatch.setattr(agent, "chat", _fake_chat(payload))
    return asyncio.run(agent.converse(user_text=text, user_id=user_id, allowed_tags=["رستوران"]))


def test_three_intents_in_one_message_make_three_cards(db, monkeypatch):
    payload = {
        "reply": "ثبت شد",
        "transcript": "۲۰۰ نون خریدم، ۵۰۰ به سعید قرض دادم، ماهی ۳ میلیون رستوران",
        "transactions": [{"title": "نان", "amount": 200000}],
        "debts": [{"kind": "credit", "counterparty": "سعید", "amount": 500000}],
        "goals": [{"topic": "رستوران", "limit_amount": 3000000}],
        "needs_data": False,
    }
    result = _run(monkeypatch, payload)

    assert len(result.created) == 1
    assert len(result.debts_created) == 1
    assert len(result.goals_created) == 1

    debt = db.get_debt(result.debts_created[0])
    assert debt["kind"] == "credit" and debt["counterparty"] == "سعید"
    assert db.get_transaction(result.created[0])["title"] == "نان"
    assert db.get_goal(result.goals_created[0])["topic"] == "رستوران"


def test_debt_only_message_creates_no_transaction(db, monkeypatch):
    payload = {
        "reply": "",
        "debts": [{"kind": "debt", "counterparty": "رضا", "amount": 1000000,
                   "due_text": "آخر ماه"}],
        "needs_data": False,
    }
    result = _run(monkeypatch, payload)

    assert result.created == [] and result.goals_created == []
    assert len(result.debts_created) == 1
    assert db.get_debt(result.debts_created[0])["due_text"] == "آخر ماه"


def test_empty_debt_items_are_ignored(db, monkeypatch):
    payload = {"reply": "باشه", "debts": [{"kind": "debt"}], "needs_data": False}
    result = _run(monkeypatch, payload)
    assert result.debts_created == [] and db.list_debts(USER) == []


def test_debt_update_by_reply_settles_existing_card(db, monkeypatch):
    debt_id = db.create_debt(user_id=USER, kind="debt", counterparty="رضا", title="بدهی رضا",
                             amount=500000, currency_display="toman", status="open")
    payload = {"reply": "تسویه شد", "debt_updates": [{"debt_id": debt_id, "settled": True}],
               "needs_data": False}
    result = _run(monkeypatch, payload)

    assert result.debts_updated == [debt_id]
    assert db.get_debt(debt_id)["status"] == "settled"


def test_goal_from_member_without_permission_is_refused_with_note(db, monkeypatch):
    hh.touch(USER, "علی")
    token = hh.create_invite(USER, "partner", False)
    hh.accept_invite(token, PARTNER, "مریم")

    payload = {"reply": "", "goals": [{"topic": "رستوران", "limit_amount": 3000000}],
               "transactions": [{"title": "نان", "amount": 200000}], "needs_data": False}
    result = _run(monkeypatch, payload, user_id=PARTNER)

    assert result.goals_created == []
    assert result.notes == [agent.NO_GOAL_PERMISSION]
    # بقیه‌ی نیت‌های همان پیام نباید قربانیِ نبودِ دسترسیِ هدف شوند.
    assert len(result.created) == 1
