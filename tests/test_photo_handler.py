"""مسیرِ تلگرامیِ عکس: هندلر، کارت‌ها، سهمیه/صفِ صبح و ثبتِ هندلر در main.

با ربات و آپدیتِ ساختگی، کلِ مسیر از «کاربر عکس فرستاد» تا «کارت‌ها رسیدند» اجرا می‌شود؛
فقط کالِ مدل جایگزین می‌شود.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.handlers import messages
from bot.llm import agent
from bot.services import memory, pending

USER = 1

RECEIPT = {"kind": "receipt", "merchant": "سوپر نمونه", "unit": "rial", "items": [
    {"name": "شیر", "line_total": 1_500_000, "raw": "شیر 1,500,000"},
    {"name": "نان", "line_total": 450_000, "raw": "نان 450,000"},
    {"name": None, "line_total": 900_000, "raw": "?? 900,000"}],
    "items_total": 2_850_000}


class _Bot:
    def __init__(self):
        self.counter = 1000
        self.sent: list[str] = []
        self.messages: list[_Msg] = []
        self.downloads: list[str] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.counter += 1
        self.sent.append(text)
        msg = _Msg(self, chat_id, self.counter, text)
        self.messages.append(msg)
        return msg

    async def get_file(self, file_id):
        self.downloads.append(file_id)

        async def _dl():
            return bytearray(b"\xff\xd8image-bytes")
        return SimpleNamespace(download_as_bytearray=_dl)

    async def edit_message_text(self, *a, **k):
        pass

    async def delete_message(self, *a, **k):
        pass


class _Msg:
    def __init__(self, bot, chat_id=USER, message_id=1, text="", photo=None, document=None,
                 caption=None):
        self._bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.text = text
        self.photo = photo or []
        self.document = document
        self.caption = caption
        self.reply_to_message = None
        self.replies: list[str] = []
        self.deleted = False
        self.edits: list[str] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        return await self._bot.send_message(self.chat_id, text, reply_markup)

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


def _update(bot, **msg_kwargs):
    msg = _Msg(bot, **msg_kwargs)
    return SimpleNamespace(
        message=msg,
        effective_chat=SimpleNamespace(id=USER),
        effective_user=SimpleNamespace(id=USER, full_name="علی"),
    )


def _photo(file_id="small", size=1000):
    return [SimpleNamespace(file_id="thumb", file_size=100),
            SimpleNamespace(file_id=file_id, file_size=size)]


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    async def _chat(messages, tools=None, json_mode=False, **kwargs):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(RECEIPT, ensure_ascii=False), tool_calls=None)

    monkeypatch.setattr(agent, "chat", _chat)
    monkeypatch.setattr(messages.goals_service, "evaluate_and_alert", _noop)
    monkeypatch.setattr(pending.goals, "evaluate_and_alert", _noop)
    return calls


async def _noop(*a, **k):
    return None


# ---------- تشخیصِ ورودی ----------

def test_photo_payload_takes_the_largest_photo():
    msg = _Msg(None, photo=_photo("big", 5000))
    assert messages.photo_payload(msg) == ("big", "image/jpeg", 5000)


def test_photo_payload_accepts_image_files_and_rejects_others():
    png = _Msg(None, document=SimpleNamespace(file_id="d1", mime_type="image/PNG", file_size=9))
    assert messages.photo_payload(png) == ("d1", "image/png", 9)
    heic = _Msg(None, document=SimpleNamespace(file_id="d2", mime_type="image/heic", file_size=9))
    assert messages.photo_payload(heic) is None
    pdf = _Msg(None, document=SimpleNamespace(file_id="d3", mime_type="application/pdf",
                                              file_size=9))
    assert messages.photo_payload(pdf) is None


def test_main_routes_photos_and_image_files_to_the_photo_handler():
    from telegram.ext import MessageHandler

    from bot.main import build_application
    app = build_application()
    handlers = [h for h in app.handlers[0] if isinstance(h, MessageHandler)]
    photo_idx = next(i for i, h in enumerate(handlers) if h.callback is messages.handle_photo)
    unsupported_idx = next(i for i, h in enumerate(handlers)
                           if h.callback is messages.handle_unsupported)
    assert photo_idx < unsupported_idx          # وگرنه Document.ALL عکسِ فایلی را رد می‌کرد
    assert "PHOTO" in str(handlers[photo_idx].filters)
    assert "PHOTO" not in str(handlers[unsupported_idx].filters)


# ---------- مسیرِ کامل ----------

def test_photo_records_each_row_and_sends_a_card_for_each(db, fake_model):
    bot = _Bot()
    update = _update(bot, photo=_photo("big"), caption="خرید امروز")
    ctx = SimpleNamespace(bot=bot, user_data={})

    asyncio.run(messages.handle_photo(update, ctx))

    assert bot.downloads == ["big"]
    replies = update.message.replies
    assert replies[0] == messages.PHOTO_STATUS
    summary = replies[1]
    assert "۳ قلم ثبت شد" in summary and "ریالی بود" in summary
    assert "۱ مورد ناقص" in summary
    cards = [t for t in bot.sent if "\n💰" in t]
    assert len(cards) == 3
    assert any("۱۵۰٬۰۰۰ تومان" in c for c in cards)
    assert any("نیازمند تکمیل: عنوان" in c for c in cards)

    # حافظه: عکس یک کوپن مصرف می‌کند و توصیفش (نه بایت‌هایش) در گفتگو می‌ماند.
    assert memory.usage_today(USER) == 1
    turns = db.recent_messages(USER, 5)
    assert turns[0]["content"].startswith("[عکس: رسید «سوپر نمونه»")
    assert "خرید امروز" in turns[0]["content"]


def test_photo_cancels_a_pending_button_edit(db, fake_model, monkeypatch):
    async def _clear(bot, awaiting):
        return None
    monkeypatch.setattr(messages, "clear_edit_messages", _clear)
    bot = _Bot()
    update = _update(bot, photo=_photo())
    ctx = SimpleNamespace(bot=bot, user_data={messages.AWAITING_KEY: {"id": 1, "action": "title"},
                                              messages.INTENT_HINT_KEY: "debt"})
    asyncio.run(messages.handle_photo(update, ctx))
    assert messages.AWAITING_KEY not in ctx.user_data
    assert messages.INTENT_HINT_KEY not in ctx.user_data      # راهنمای نیت به پیامِ بعدی نشت نکند
    assert "ویرایش قبلی لغو شد" in update.message.replies[0]


def test_oversized_image_file_is_rejected_before_download(db, fake_model, set_setting):
    set_setting("max_image_bytes", 10)
    bot = _Bot()
    update = _update(bot, photo=_photo(size=11))
    asyncio.run(messages.handle_photo(update, SimpleNamespace(bot=bot, user_data={})))
    assert update.message.replies == [messages.IMAGE_TOO_BIG]
    assert bot.downloads == [] and fake_model == []


def test_model_outage_is_reported_and_nothing_is_recorded(db, monkeypatch):
    from bot.llm.client import USER_FACING_UNAVAILABLE, LLMUnavailableError

    async def _down(*a, **k):
        raise LLMUnavailableError("all models failed")
    monkeypatch.setattr(agent, "chat", _down)
    bot = _Bot()
    update = _update(bot, photo=_photo())
    asyncio.run(messages.handle_photo(update, SimpleNamespace(bot=bot, user_data={})))
    status = next(m for m in bot.messages if m.text == messages.PHOTO_STATUS)
    assert status.edits == [USER_FACING_UNAVAILABLE]
    assert db.list_user_transactions(USER, None, include_drafts=True) == []


# ---------- بعد از پایانِ سهمیه: صف و ثبتِ صبح ----------

def test_photo_after_quota_is_queued_then_recorded_by_the_morning_flush(db, fake_model,
                                                                        set_setting):
    set_setting("daily_llm_limit", 0)
    bot = _Bot()
    update = _update(bot, photo=_photo("queued-photo"), caption="مالِ دیروز")
    asyncio.run(messages.handle_photo(update, SimpleNamespace(bot=bot, user_data={})))

    assert update.message.replies == [messages.OFF_HOURS_MSG]
    queued = db.get_pending(USER)
    assert queued[0]["kind"] == "photo"
    assert json.loads(queued[0]["content"]) == {"file_id": "queued-photo", "mime": "image/jpeg",
                                                "caption": "مالِ دیروز"}
    assert fake_model == []                                   # هیچ کالی بعد از سهمیه

    morning = _Bot()
    assert asyncio.run(pending.flush_pending(morning, USER)) is True
    assert morning.downloads == ["queued-photo"]
    assert len(fake_model) == 1                               # فقط کالِ عکس؛ کالِ دسته‌ایِ خالی نه
    assert "مالِ دیروز" in json.dumps(fake_model[0], ensure_ascii=False)
    assert len([t for t in morning.sent if "\n💰" in t]) == 3
    assert any("۳ قلم ثبت شد" in t for t in morning.sent)
    assert db.get_pending(USER) == []


def test_morning_flush_with_text_and_photo_runs_both(db, fake_model, monkeypatch):
    batch_calls = []

    async def _batch(**kwargs):
        batch_calls.append(kwargs)
        return agent.AgentResult(reply="ثبت شد", created=[])
    monkeypatch.setattr(agent, "converse_batch", _batch)
    db.add_pending(USER, "text", "۲۰۰ نون")
    db.add_pending(USER, "photo", json.dumps({"file_id": "p1", "mime": "image/jpeg",
                                              "caption": ""}))
    db.add_pending(USER, "photo", "{broken json")             # ردیفِ خراب نباید کل صف را بخواباند

    bot = _Bot()
    assert asyncio.run(pending.flush_pending(bot, USER)) is True
    assert batch_calls[0]["text_parts"] == ["۲۰۰ نون"]
    assert bot.downloads == ["p1"]
    assert db.get_pending(USER) == []


def test_card_send_waits_out_telegram_rate_limit(monkeypatch):
    """۱۵ کارتِ پشتِ سرِ هم ممکن است RetryAfter بگیرد؛ کارت نباید گم شود."""
    from telegram.error import RetryAfter

    from bot.handlers import cards

    slept = []

    async def _sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(cards.asyncio, "sleep", _sleep)

    class _FlakyBot:
        def __init__(self):
            self.calls = 0

        async def send_message(self, chat_id, text, reply_markup=None):
            self.calls += 1
            if self.calls == 1:
                raise RetryAfter(3)
            return SimpleNamespace(message_id=7)

    bot = _FlakyBot()
    msg = asyncio.run(cards._send(bot, USER, "📝 شیر", None))
    assert msg.message_id == 7 and bot.calls == 2 and slept == [3.0]
