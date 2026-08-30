"""وقتی نیت مبهم است، ربات می‌پرسد — نه اینکه حدس بزند.

حدسِ اشتباه بینِ «خرج» و «بدهی» هزینه دارد: کاربر باید رکوردِ غلط را پیدا و پاک کند.
پس موردِ مبهم اصلاً ثبت نمی‌شود؛ می‌ماند تا با یک دکمه تعیین تکلیف شود.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.llm import agent
from bot.services import clarify

USER, OTHER = 1, 2

AMBIGUOUS = {
    "reply": "",
    "transactions": [],
    "debts": [],
    "clarify": [{"title": "پول رضا", "amount": 500000, "counterparty": "رضا"}],
    "needs_data": False,
}


def _fake_chat(payload):
    async def _chat(messages, tools=None, json_mode=False):
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=None)
    return _chat


def _extract(monkeypatch, payload, user_id=USER):
    monkeypatch.setattr(agent, "chat", _fake_chat(payload))
    return asyncio.run(agent.converse(user_text="پول رضا رو دادم", user_id=user_id,
                                      allowed_tags=[]))


def test_ambiguous_item_records_nothing_yet(db, monkeypatch):
    result = _extract(monkeypatch, AMBIGUOUS)

    assert result.created == [] and result.debts_created == []
    assert len(result.clarifications) == 1
    assert db.list_user_transactions(USER) == []
    assert db.list_debts(USER) == []


def test_question_names_the_item_so_the_user_knows_which(db, monkeypatch):
    clar_id = _extract(monkeypatch, AMBIGUOUS).clarifications[0]
    question = clarify.question(clar_id)

    assert "پول رضا" in question
    assert "۵۰۰٬۰۰۰" in question
    assert "رضا" in question


def test_choosing_expense_creates_a_transaction(db, monkeypatch):
    clar_id = _extract(monkeypatch, AMBIGUOUS).clarifications[0]

    kind, obj_id = clarify.resolve(USER, clar_id, clarify.CHOICE_TXN)

    assert kind == clarify.CHOICE_TXN
    assert db.get_transaction(obj_id)["amount"] == 500000
    assert db.list_debts(USER) == []


def test_choosing_debt_creates_a_debt(db, monkeypatch):
    clar_id = _extract(monkeypatch, AMBIGUOUS).clarifications[0]

    kind, obj_id = clarify.resolve(USER, clar_id, clarify.CHOICE_DEBT)

    assert kind == clarify.CHOICE_DEBT
    debt = db.get_debt(obj_id)
    assert debt["counterparty"] == "رضا" and debt["amount"] == 500000
    assert db.list_user_transactions(USER) == []


def test_answering_twice_does_nothing_the_second_time(db, monkeypatch):
    """دابل‌کلیک روی دکمه نباید دو رکورد بسازد."""
    clar_id = _extract(monkeypatch, AMBIGUOUS).clarifications[0]

    first = clarify.resolve(USER, clar_id, clarify.CHOICE_TXN)
    second = clarify.resolve(USER, clar_id, clarify.CHOICE_DEBT)

    assert first[0] == clarify.CHOICE_TXN
    assert second == (None, None)
    assert len(db.list_user_transactions(USER)) == 1


def test_another_user_cannot_answer_your_question(db, monkeypatch):
    clar_id = _extract(monkeypatch, AMBIGUOUS).clarifications[0]
    assert clarify.resolve(OTHER, clar_id, clarify.CHOICE_TXN) == (None, None)


def test_empty_ambiguous_items_are_ignored(db, monkeypatch):
    payload = dict(AMBIGUOUS, clarify=[{"title": None, "amount": None, "counterparty": None}])
    assert _extract(monkeypatch, payload).clarifications == []


@pytest.mark.parametrize("rule", [
    "اسنپ‌پی",              # نامِ سرویس، شخص نیست
    "پرداخت کردم",          # فعلِ پرداخت بر کلمه‌ی بدهی اولویت دارد
    "شخصِ حقیقیِ نام‌برده‌شده",
])
def test_prompt_states_the_new_intent_rules(rule):
    """قواعدِ تفکیک باید در پرامپت صریح باشند، نه ضمنی."""
    from bot.llm.prompts import EXTRACT_SYSTEM
    assert rule in EXTRACT_SYSTEM
