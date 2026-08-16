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


def test_door_stays_shut_without_url_or_static_token(set_setting):
    from bot.config import settings
    assert _parse_ingest_tokens("") == {}

    set_setting("ingest_tokens", {})
    set_setting("ingest_public_url", "")
    assert settings.ingest_enabled is False

    # آدرسِ عمومی به‌تنهایی کافی است — توکن‌ها از /shortcut می‌آیند.
    set_setting("ingest_public_url", "http://example.com:8081")
    assert settings.ingest_enabled is True


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


# ---------- توکنِ دیتابیسی (مسیرِ اسکیل‌پذیر) ----------

def test_issued_token_resolves_without_touching_env(ingest_env, db, set_setting):
    """کاربرِ تازه با /shortcut توکن می‌گیرد — بدونِ ویرایشِ .env و بدونِ ری‌استارت."""
    set_setting("ingest_tokens", {})           # هیچ توکنِ ثابتی در .env نیست
    set_setting("allowed_user_ids", frozenset({USER}))

    token = ingest_env.issue_token(USER)
    assert len(token) >= 24
    assert ingest_env.resolve_user(token) == USER


def test_issuing_again_revokes_the_previous_token(ingest_env, db, set_setting):
    set_setting("ingest_tokens", {})
    old = ingest_env.issue_token(USER)
    new = ingest_env.issue_token(USER)

    assert old != new
    assert ingest_env.resolve_user(new) == USER
    with pytest.raises(ingest_env.IngestAuthError):
        ingest_env.resolve_user(old)


def test_issued_token_stops_working_when_access_is_revoked(ingest_env, db, set_setting):
    set_setting("ingest_tokens", {})
    token = ingest_env.issue_token(USER)
    set_setting("allowed_user_ids", frozenset({999}))
    with pytest.raises(ingest_env.IngestAuthError):
        ingest_env.resolve_user(token)


# ---------- کلیدِ خودکارِ تکراری‌نشدن ----------

def test_fallback_request_id_is_stable_for_identical_input(ingest_env):
    a = ingest_env.fallback_request_id(USER, "قهوه ۹۰ تومن", None)
    b = ingest_env.fallback_request_id(USER, "قهوه ۹۰ تومن", None)
    c = ingest_env.fallback_request_id(USER, "چای ۹۰ تومن", None)
    d = ingest_env.fallback_request_id(999, "قهوه ۹۰ تومن", None)

    assert a == b          # همان حرف، دوباره فرستاده شد → تراکنشِ تکراری نمی‌سازد
    assert a != c          # حرفِ دیگر → ثبتِ جدا
    assert a != d          # کاربرِ دیگر → ثبتِ جدا


def test_fallback_request_id_separates_audio_from_text(ingest_env):
    text_key = ingest_env.fallback_request_id(USER, "", None)
    audio_key = ingest_env.fallback_request_id(USER, "", (b"blob", "m4a"))
    assert text_key != audio_key


# ---------- قالبِ پاسخِ HTTP ----------
#
# مسیرِ میان‌بر (`/s/<token>`) باید **متنِ ساده** بدهد، نه JSON: خروجی‌اش مستقیم در
# Show Notification می‌نشیند و اگر JSON باشد کاربر یک نوتیفیکیشنِ پر از `{"ok":true,…}`
# می‌بیند و برای تمیزکردنش باید دو اکشنِ اضافه به میان‌بر بیفزاید.

def _serve(bot, method, target, body=b""):
    """یک درخواستِ HTTP کامل را به هندلرِ سرور می‌دهد و پاسخِ خام را برمی‌گرداند."""
    from bot.ingest import server

    class Reader:
        def __init__(self, data): self._data = data
        async def readuntil(self, sep):
            head, _, rest = self._data.partition(sep)
            self._data = rest
            return head + sep
        async def readexactly(self, n):
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    class Writer:
        def __init__(self): self.chunks = []
        def write(self, data): self.chunks.append(data)
        async def drain(self): pass
        def close(self): pass

    request = (f"{method} {target} HTTP/1.1\r\nContent-Length: {len(body)}\r\n"
               "\r\n").encode() + body
    writer = Writer()
    asyncio.run(server._client(bot, Reader(request), writer))
    return b"".join(writer.chunks).decode("utf-8")


def _split(raw):
    head, _, body = raw.partition("\r\n\r\n")
    return head, body


def test_shortcut_route_answers_plain_text_not_json(ingest_env, db, monkeypatch):
    _fake_extract(monkeypatch, {
        "transcript": "قهوه ۹۰ تومن",
        "transactions": [{"title": "قهوه", "amount": 90000, "currency": "toman"}],
    })
    head, body = _split(_serve(FakeBot(), "POST", "/s/" + "t" * 32,
                               "قهوه ۹۰ تومن".encode("utf-8")))

    assert "200 OK" in head
    assert "text/plain" in head
    assert body == ingest_env.MSG_RECORDED       # دقیقاً همان یک خط، بدون آکولاد
    assert "{" not in body


def test_shortcut_route_reports_errors_as_plain_text_too(ingest_env, db):
    """پیامِ خطا هم روی نوتیفیکیشن می‌نشیند، پس نباید JSON باشد."""
    head, body = _split(_serve(FakeBot(), "POST", "/s/wrong-token", b"x"))

    assert "401" in head
    assert "text/plain" in head
    assert "{" not in body


def test_programmatic_route_still_answers_json(ingest_env, db, monkeypatch):
    """`/ingest` کالرش کد است، نه چشمِ آدم — قالبش JSON می‌ماند."""
    _fake_extract(monkeypatch, {
        "transcript": "نان ۳۰ تومن",
        "transactions": [{"title": "نان", "amount": 30000, "currency": "toman"}],
    })
    payload = json.dumps({"token": "t" * 32, "request_id": "json-1", "text": "نان ۳۰ تومن"})
    head, body = _split(_serve(FakeBot(), "POST", "/ingest", payload.encode("utf-8")))

    assert "200 OK" in head
    assert "application/json" in head
    assert json.loads(body)["status"] == "recorded"


def test_health_stays_json(ingest_env, db):
    head, body = _split(_serve(FakeBot(), "GET", "/health"))
    assert "application/json" in head
    assert json.loads(body) == {"ok": True}
