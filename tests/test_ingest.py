"""درِ دومِ ورودی (میان‌برِ iOS): احراز هویت، idempotency، و باریک‌بودنِ مسیر.

مهم‌ترین چیزی که اینجا محافظت می‌شود: از این مسیر **فقط تراکنش** ثبت می‌شود. بدهی،
هدف و ویرایشِ تراکنشِ قبلی نباید هیچ اثری روی دیتابیس بگذارند، چون کاربر آن‌طرف کارت و
دکمه‌ی تأیید جلوی چشمش ندارد.
"""
import asyncio
import json

import pytest

from bot.config import _parse_ingest_tokens

USER = 4242


class FakeBot:
    """فقط چیزی که ingest از bot می‌خواهد: فرستادن پیام."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[int, str]] = []
        self.fail = fail
        self._next_id = 100

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("تلگرام در دسترس نیست")
        self.sent.append((chat_id, text))
        self._next_id += 1
        return type("Msg", (), {"message_id": self._next_id})()


@pytest.fixture
def ingest_env(db, set_setting):
    """کاربر مجاز + توکن معتبر + سهمیه‌ی باز."""
    from bot.services import ingest
    from bot.utils import ratelimit

    set_setting("ingest_tokens", {"t" * 32: USER})
    set_setting("allowed_user_ids", frozenset({USER}))
    set_setting("daily_llm_limit", 100)
    ratelimit.reset(USER)
    return ingest


def _fake_extract(monkeypatch, payload: dict):
    """پاسخِ خامِ مدل را جایگزین می‌کند تا مسیرِ واقعیِ استخراج اجرا شود."""
    from bot.llm import agent

    async def fake_chat(messages, tools=None, json_mode=False):
        return type("Msg", (), {"content": json.dumps(payload, ensure_ascii=False)})()

    monkeypatch.setattr(agent, "chat", fake_chat)


def _run(coro):
    return asyncio.run(coro)


# ---------- توکن ----------

def test_token_parsing_rejects_short_and_malformed():
    tokens = _parse_ingest_tokens(f"1:{'a' * 32}, 2:short, junk, :{'b' * 32}, 3:{'c' * 24}")
    assert tokens == {"a" * 32: 1, "c" * 24: 3}


def test_empty_token_config_disables_the_door():
    from bot.config import Settings
    assert _parse_ingest_tokens("") == {}
    assert Settings.ingest_enabled.fget(type("S", (), {"ingest_tokens": {}})()) is False


def test_resolve_user_rejects_bad_token(ingest_env):
    with pytest.raises(ingest_env.IngestAuthError):
        ingest_env.resolve_user("wrong-token")
    assert ingest_env.resolve_user("t" * 32) == USER


def test_resolve_user_rejects_unauthorized_user(ingest_env, set_setting):
    set_setting("allowed_user_ids", frozenset({999}))
    with pytest.raises(ingest_env.IngestAuthError):
        ingest_env.resolve_user("t" * 32)


# ---------- ثبت ----------

def test_text_creates_transaction_and_sends_card(ingest_env, db, monkeypatch):
    _fake_extract(monkeypatch, {
        "reply": "ثبت شد", "transcript": "ناهار ۲۵۰ هزار تومن",
        "transactions": [{"title": "ناهار", "amount": 250000, "currency": "toman"}],
    })
    bot = FakeBot()
    result = _run(ingest_env.handle(bot, user_id=USER, request_id="req-1",
                                    text="ناهار ۲۵۰ هزار تومن"))

    assert result.status == "recorded"
    rows = db.list_user_transactions(USER)
    assert len(rows) == 1
    assert rows[0]["amount"] == 250000
    assert rows[0]["source"] == "shortcut"
    assert bot.sent, "کارت باید به تلگرام رفته باشد"


def test_audio_path_reaches_the_model(ingest_env, db, monkeypatch):
    seen = {}
    from bot.llm import agent

    async def fake_chat(messages, tools=None, json_mode=False):
        seen["content"] = messages[-1]["content"]
        return type("Msg", (), {"content": json.dumps(
            {"transcript": "تاکسی ۸۰ تومن",
             "transactions": [{"title": "تاکسی", "amount": 80000, "currency": "toman"}]})})()

    monkeypatch.setattr(agent, "chat", fake_chat)
    result = _run(ingest_env.handle(FakeBot(), user_id=USER, request_id="req-audio",
                                    audio=(b"fake-audio-bytes", "m4a")))

    assert result.status == "recorded"
    audio_parts = [p for p in seen["content"] if p.get("type") == "input_audio"]
    assert len(audio_parts) == 1
    assert audio_parts[0]["input_audio"]["format"] == "m4a"


def test_unknown_audio_format_falls_back_to_m4a(ingest_env):
    assert ingest_env.normalize_audio_format("M4A") == "m4a"
    assert ingest_env.normalize_audio_format(".ogg") == "ogg"
    assert ingest_env.normalize_audio_format("flac") == "m4a"
    assert ingest_env.normalize_audio_format("") == "m4a"


# ---------- مسیر باید باریک بماند ----------

def test_debts_and_goals_are_not_written_from_shortcut(ingest_env, db, monkeypatch):
    _fake_extract(monkeypatch, {
        "transcript": "۵۰۰ به سعید قرض دادم و سقف رستوران رو بذار ۲ میلیون",
        "transactions": [],
        "debts": [{"kind": "receivable", "counterparty": "سعید", "amount": 500000}],
        "goals": [{"topic": "رستوران", "limit_amount": 2000000}],
    })
    bot = FakeBot()
    result = _run(ingest_env.handle(bot, user_id=USER, request_id="req-debt",
                                    text="۵۰۰ به سعید قرض دادم"))

    assert result.status == "nothing"
    assert db.list_debts(USER) == []
    assert db.list_user_transactions(USER) == []
    # ورودی نباید بی‌صدا گم شود: متنش باید در تلگرام رفته باشد.
    assert any("سعید" in text for _, text in bot.sent)


def test_transaction_recorded_but_debt_dropped_is_reported(ingest_env, db, monkeypatch):
    _fake_extract(monkeypatch, {
        "transcript": "قهوه ۹۰ تومن و ۵۰۰ به سعید قرض دادم",
        "transactions": [{"title": "قهوه", "amount": 90000, "currency": "toman"}],
        "debts": [{"kind": "receivable", "counterparty": "سعید", "amount": 500000}],
    })
    bot = FakeBot()
    result = _run(ingest_env.handle(bot, user_id=USER, request_id="req-mixed",
                                    text="قهوه ۹۰ تومن و ۵۰۰ به سعید قرض دادم"))

    assert result.status == "recorded"
    assert len(db.list_user_transactions(USER)) == 1
    assert db.list_debts(USER) == []
    assert any("بدهی/طلب" in text for _, text in bot.sent)


# ---------- تکراری ----------

def test_same_request_id_does_not_double_record(ingest_env, db, monkeypatch):
    _fake_extract(monkeypatch, {
        "transcript": "نان ۳۰ تومن",
        "transactions": [{"title": "نان", "amount": 30000, "currency": "toman"}],
    })
    bot = FakeBot()
    first = _run(ingest_env.handle(bot, user_id=USER, request_id="dup", text="نان ۳۰ تومن"))
    second = _run(ingest_env.handle(bot, user_id=USER, request_id="dup", text="نان ۳۰ تومن"))

    assert first.status == "recorded"
    assert second.status == "duplicate"
    assert len(db.list_user_transactions(USER)) == 1


def test_failed_request_can_be_retried_with_same_id(ingest_env, db, monkeypatch):
    from bot.llm import agent
    from bot.llm.client import LLMUnavailableError

    async def boom(messages, tools=None, json_mode=False):
        raise LLMUnavailableError("down")

    monkeypatch.setattr(agent, "chat", boom)
    failed = _run(ingest_env.handle(FakeBot(), user_id=USER, request_id="retry", text="نان"))
    assert failed.status == "error"

    _fake_extract(monkeypatch, {
        "transcript": "نان ۳۰ تومن",
        "transactions": [{"title": "نان", "amount": 30000, "currency": "toman"}],
    })
    from bot.utils import ratelimit
    ratelimit.reset(USER)
    again = _run(ingest_env.handle(FakeBot(), user_id=USER, request_id="retry", text="نان ۳۰ تومن"))
    assert again.status == "recorded"
    assert len(db.list_user_transactions(USER)) == 1


# ---------- سقفِ روزانه ----------

def test_over_quota_input_is_queued_not_dropped(ingest_env, db, set_setting):
    set_setting("daily_llm_limit", 0)

    result = _run(ingest_env.handle(FakeBot(), user_id=USER, request_id="q1", text="نان ۳۰ تومن"))

    assert result.status == "queued"
    assert db.has_pending(USER)
    assert db.get_pending(USER)[0]["kind"] == "text"


def test_over_quota_audio_is_saved_to_disk(ingest_env, db, set_setting, tmp_path):
    set_setting("daily_llm_limit", 0)
    set_setting("ingest_audio_dir", str(tmp_path / "audio"))

    result = _run(ingest_env.handle(FakeBot(), user_id=USER, request_id="q2",
                                    audio=(b"bytes", "m4a")))

    assert result.status == "queued"
    row = db.get_pending(USER)[0]
    assert row["kind"] == "voice_file"
    assert row["content"].endswith(".m4a")
    with open(row["content"], "rb") as fh:
        assert fh.read() == b"bytes"


# ---------- کارتِ جامانده ----------

def test_card_survives_telegram_being_down(ingest_env, db, monkeypatch):
    """اگر تلگرام لحظه‌ی ثبت در دسترس نبود، تراکنش می‌ماند و کارت بعداً می‌رود."""
    _fake_extract(monkeypatch, {
        "transcript": "بنزین ۱۰۰ تومن",
        "transactions": [{"title": "بنزین", "amount": 100000, "currency": "toman"}],
    })
    result = _run(ingest_env.handle(FakeBot(fail=True), user_id=USER,
                                    request_id="offline", text="بنزین ۱۰۰ تومن"))

    assert result.status == "recorded"
    rows = db.list_user_transactions(USER)
    assert len(rows) == 1

    undelivered = db.undelivered_cards("shortcut", "2000-01-01T00:00:00")
    assert [r["id"] for r in undelivered] == [rows[0]["id"]]

    # حالا که تلگرام برگشته، job دوره‌ای کارت را می‌فرستد و دیگر جامانده نیست.
    bot = FakeBot()
    _run(ingest_env.deliver_undelivered_cards(type("Ctx", (), {"bot": bot})()))
    assert bot.sent
    assert db.undelivered_cards("shortcut", "2000-01-01T00:00:00") == []
