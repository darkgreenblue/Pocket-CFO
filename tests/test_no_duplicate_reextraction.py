"""ثبتِ ششمِ روز نباید پنج خرجِ قبلی را دوباره بسازد.

باگِ واقعی: مسیرهای یک‌شات (شرتکاتِ آیفون و صفِ صبح) تاریخچه‌ی همان روز را به مدلِ
استخراج می‌دادند. آن تاریخچه رشته‌ای از پیام‌های کاربر بود که هرکدام یک خرج را می‌گفتند
و — چون مسیرِ شرتکات طرفِ دستیار را ذخیره نمی‌کرد — هیچ نشانه‌ای نداشت که قبلاً ثبت
شده‌اند. مدل کلِ آن فهرست را «خرج‌هایی برای ثبت» می‌دید و همه را از نو می‌ساخت: کاربر
خرجِ ششم را می‌فرستاد و شش کارتِ تازه می‌گرفت و باید پنج‌تا را دستی پاک می‌کرد.

درمان ریشه‌ای است، نه علامتی: به این دو مسیر اصلاً تاریخچه داده نمی‌شود.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.llm import agent
from bot.services import memory

USER = 1


class _Recorder:
    """پاسخِ ثابت می‌دهد و پیام‌هایی که به مدل رفته را نگه می‌دارد."""

    def __init__(self, payload):
        self.payload = payload
        self.seen_messages = None

    async def __call__(self, messages, tools=None, json_mode=False):
        self.seen_messages = messages
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False),
                               tool_calls=None)

    @property
    def history_turns(self):
        """پیام‌های غیرِ system و غیرِ آخرین پیام — یعنی همان تاریخچه."""
        return [m for m in (self.seen_messages or [])[1:-1]]


def _fill_todays_memory():
    """پنج خرجِ ثبت‌شده‌ی امروز، همان‌طور که در حافظه می‌نشینند."""
    for i, spoken in enumerate(["۲۰۰ تومن نون خریدم", "۵۰۰ تاکسی", "۱۲۰ قهوه",
                                "۸۰۰ سوپرمارکت", "۳۰۰ داروخانه"], start=1):
        memory.remember(USER, "user", spoken)


@pytest.fixture
def one_new_expense():
    return {"reply": "ثبت شد", "transcript": "۱۵۰ نان",
            "transactions": [{"title": "نان", "amount": 150000}], "needs_data": False}


def test_shortcut_extraction_never_sees_todays_history(db, monkeypatch, one_new_expense):
    _fill_todays_memory()
    recorder = _Recorder(one_new_expense)
    monkeypatch.setattr(agent, "chat", recorder)

    result = asyncio.run(agent.converse_expense_only(
        user_text="۱۵۰ نان", user_id=USER, allowed_tags=[]))

    assert recorder.history_turns == [], recorder.history_turns
    assert len(result.created) == 1          # فقط خرجِ تازه، نه شش‌تا


def test_queue_flush_extraction_never_sees_todays_history(db, monkeypatch, one_new_expense):
    _fill_todays_memory()
    recorder = _Recorder(one_new_expense)
    monkeypatch.setattr(agent, "chat", recorder)

    asyncio.run(agent.converse_batch(
        text_parts=["۱۵۰ نان"], audio_items=[], user_id=USER, allowed_tags=[]))

    assert recorder.history_turns == []


def test_shortcut_records_an_assistant_turn_so_the_day_reads_as_answered(db, monkeypatch):
    """حافظه‌ی روز نباید رشته‌ای از پیام‌های بی‌جوابِ کاربر باشد."""
    from bot.services import ingest as ingest_service

    async def _fake(**kwargs):
        return agent.AgentResult(reply="", transcript="۱۵۰ نان", created=[1])

    monkeypatch.setattr(agent, "converse_expense_only", _fake)

    class _Bot:
        async def send_message(self, **kwargs):
            return SimpleNamespace(message_id=1, chat_id=USER)

    request_id = "req-test-1"
    assert ingest_service.repo.claim_ingest_request(request_id, USER) is True

    result = asyncio.run(ingest_service._process(_Bot(), USER, request_id, "۱۵۰ نان", None))
    assert result.status == "recorded"

    turns = ingest_service.repo.recent_messages(USER, 10)
    assert [t["role"] for t in turns] == ["user", "assistant"], turns
    assert "ثبت شد" in turns[1]["content"]


def test_conversational_path_still_gets_history(db, monkeypatch, one_new_expense):
    """تاریخچه فقط از مسیرهای یک‌شات برداشته شد؛ گفتگوی تلگرام هنوز به آن نیاز دارد."""
    _fill_todays_memory()
    recorder = _Recorder(one_new_expense)
    monkeypatch.setattr(agent, "chat", recorder)

    asyncio.run(agent.converse(user_text="۱۵۰ نان", user_id=USER,
                               history=memory.history(USER), allowed_tags=[]))

    assert recorder.history_turns, "مسیر گفتگویی بدون کانتکست کار نمی‌کند"


def test_extraction_prompt_forbids_reusing_history():
    """پرامپت باید صریحاً بگوید فقط از آخرین پیام استخراج کن (خطِ دفاعیِ دوم)."""
    from bot.llm.prompts import EXTRACT_SYSTEM
    assert "آخرین پیامِ کاربر" in EXTRACT_SYSTEM
    assert "دوباره نسازش" in EXTRACT_SYSTEM
