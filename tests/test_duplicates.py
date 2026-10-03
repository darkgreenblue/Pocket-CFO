"""تشخیصِ «احتمالاً تکراری» — قاعده‌ی ثابت، بدونِ LLM.

سناریوی واقعی که این تست‌ها نگهبانش‌اند: کاربر خرجی را با ویس گفته و بعد اسکرین‌شاتِ
پیامک‌های بانک را می‌فرستد که همان خرج هم در آن است (یا دو اسکرین‌شاتِ هم‌پوشان). همه
ثبت می‌شوند، ولی کارتِ مشکوک خطِ «⚠️ احتمالاً تکراریِ #X» و دکمه‌ی «🗑 تکراریه، حذف» می‌گیرد.
"""
import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace


from bot.handlers import callbacks, cards, messages
from bot.llm import agent, vision
from bot.services import duplicates as dup
from bot.services import pending
from bot.services import transactions as ts

USER = 1
OTHER_HOUSEHOLD_USER = 2
TODAY = date.today()


def _txn(id_, amount, *, days_ago=0, occurred=None, currency="toman"):
    created = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time())
    return {"id": id_, "amount": amount, "currency_display": currency,
            "created_at": created.isoformat(), "occurred_on": occurred}


def _create(amount, *, title="نان", days_ago=0, user=USER, source="chat", occurred_on=None,
            currency=None):
    tid = ts.create_from_item(user, {"title": title, "amount": amount, "currency": currency},
                              source=source, occurred_on=occurred_on)
    if days_ago:
        from bot.db import repo
        when = datetime.now() - timedelta(days=days_ago)
        repo.update_transaction(tid, created_at=when.isoformat())
    return tid


# ---------- قاعده‌ی تطبیق (تابعِ خالص) ----------

def test_exact_amount_same_day_matches():
    assert dup.find_match(_txn(9, 64_000), [_txn(1, 64_000)])["id"] == 1


def test_two_percent_tolerance_covers_rounded_voice_amounts():
    """«۲۰۰ تومن»ِ ویس در برابرِ ۱۹۸٬۵۰۰ِ پیامک (۰٫۷۵٪) → تکراری؛ ۳٪ اختلاف → نه."""
    assert dup.find_match(_txn(9, 198_500), [_txn(1, 200_000)]) is not None
    assert dup.find_match(_txn(9, 194_000), [_txn(1, 200_000)]) is None
    assert dup.amounts_close(100, 102) and not dup.amounts_close(100, 103)


def test_currency_must_match():
    assert dup.find_match(_txn(9, 50, currency="usd"), [_txn(1, 50)]) is None


def test_window_is_two_days_either_way():
    assert dup.find_match(_txn(9, 50_000), [_txn(1, 50_000, days_ago=2)]) is not None
    assert dup.find_match(_txn(9, 50_000), [_txn(1, 50_000, days_ago=3)]) is None
    assert dup.find_match(_txn(9, 50_000, days_ago=2), [_txn(1, 50_000)]) is not None


def test_real_purchase_date_beats_recording_date():
    """پیامکِ ۴ روز پیش که امروز اسکرین‌شات شده، با ویسِ همان ۴ روز پیش جور است."""
    four_days_ago = (TODAY - timedelta(days=4)).isoformat()
    sms_row = _txn(9, 64_000, occurred=four_days_ago)
    assert dup.find_match(sms_row, [_txn(1, 64_000, days_ago=4)]) is not None
    assert dup.find_match(sms_row, [_txn(1, 64_000)]) is None       # ویسِ امروز → ۴ روز فاصله


def test_missing_amounts_never_match():
    assert dup.find_match(_txn(9, None), [_txn(1, 64_000)]) is None
    assert dup.find_match(_txn(9, 64_000), [_txn(1, None)]) is None


def test_closest_amount_wins_and_claimed_ones_are_skipped():
    cands = [_txn(1, 200_000), _txn(2, 198_000), _txn(3, 198_600)]
    assert dup.find_match(_txn(9, 198_500), cands)["id"] == 3
    assert dup.find_match(_txn(9, 198_500), cands, claimed={3})["id"] == 2


# ---------- علامت‌گذاری روی دیتابیس ----------

def test_sms_row_after_voice_is_flagged(db):
    voice = _create(64_000)
    rows = [_create(64_000, title=None, source="photo"), _create(380_000, title=None, source="photo")]

    found = dup.flag_new(USER, rows)

    assert found == {rows[0]: voice}
    assert db.get_transaction(rows[0])["duplicate_of"] == voice
    assert db.get_transaction(rows[1])["duplicate_of"] is None


def test_rows_of_the_same_message_are_not_compared(db):
    """دو تاکسیِ ۵۰ تومنی در یک ویس دو خرجِ واقعی‌اند."""
    batch = [_create(50_000, title="تاکسی"), _create(50_000, title="تاکسی")]
    assert dup.flag_new(USER, batch) == {}


def test_one_previous_record_flags_only_one_new_row(db):
    prior = _create(50_000, title="تاکسی")
    batch = [_create(50_000, title=None, source="photo"), _create(50_000, title=None, source="photo")]
    found = dup.flag_new(USER, batch)
    assert list(found.values()) == [prior] and len(found) == 1


def test_overlapping_screenshots_are_caught_by_purchase_date(db):
    """اسکرین‌شاتِ دیروز و امروز هر دو پیامکِ ۳ روز پیش را دارند."""
    sms_day = (TODAY - timedelta(days=3)).isoformat()
    first = _create(1_250_000, title=None, source="photo", occurred_on=sms_day, days_ago=1)
    second = _create(1_250_000, title=None, source="photo", occurred_on=sms_day)
    assert dup.flag_new(USER, [second]) == {second: first}


def test_other_households_are_invisible(db):
    _create(64_000, user=OTHER_HOUSEHOLD_USER)
    assert dup.flag_new(USER, [_create(64_000)]) == {}


def test_flagging_never_breaks_recording(db, monkeypatch):
    new = _create(64_000)

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(dup.repo, "list_user_transactions", _boom)
    assert dup.flag_new(USER, [new]) == {}


# ---------- کارت ----------

def _buttons(keyboard):
    return {b.callback_data: b.text for row in keyboard.inline_keyboard for b in row}


def test_flagged_card_shows_warning_and_duplicate_delete_label(db):
    original = _create(64_000, title="نان")
    copy = _create(64_000, title=None, source="photo")
    dup.flag_new(USER, [copy])

    text, keyboard = cards.render_txn(db.get_transaction(copy))
    assert f"⚠️ احتمالاً تکراریِ #{original} «نان»" in text and "امروز" in text
    assert _buttons(keyboard)[f"delete:{copy}"] == "🗑 تکراریه، حذف"

    plain_text, plain_kb = cards.render_txn(db.get_transaction(original))
    assert "تکراری" not in plain_text
    assert _buttons(plain_kb)[f"delete:{original}"] == "🗑 حذف"


class _Bot:
    def __init__(self):
        self.edited: list[tuple] = []
        self.sent: list[str] = []

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        self.edited.append((message_id, text))

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=900 + len(self.sent), chat_id=chat_id)


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = SimpleNamespace(chat_id=USER, message_id=77)
        self.answers: list[str] = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        pass


def test_deleting_the_original_clears_the_warning_on_the_copy(db):
    original = _create(64_000, title="نان")
    copy = _create(64_000, title=None, source="photo")
    dup.flag_new(USER, [copy])
    db.set_card_message(copy, USER, 555)

    bot = _Bot()
    update = SimpleNamespace(callback_query=_Query(f"delete:{original}"),
                             effective_user=SimpleNamespace(id=USER, full_name="علی"),
                             effective_chat=SimpleNamespace(id=USER))
    asyncio.run(callbacks.on_callback(update, SimpleNamespace(bot=bot, user_data={})))

    assert db.get_transaction(original) is None
    assert db.get_transaction(copy)["duplicate_of"] is None
    refreshed = [t for mid, t in bot.edited if mid == 555]
    assert refreshed and "تکراری" not in refreshed[-1]


# ---------- مسیرهای واقعی ----------

RECEIPT = {"kind": "receipt", "merchant": "سوپر نمونه", "unit": "rial", "items": [
    {"name": "شیر", "line_total": 1_500_000}, {"name": "نان", "line_total": 450_000},
    {"name": "پنیر", "line_total": 900_000}], "items_total": 2_850_000}


def _fake_model(monkeypatch, payload):
    async def _chat(messages_, tools=None, json_mode=False, **kwargs):
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=None)
    monkeypatch.setattr(agent, "chat", _chat)


async def _noop(*a, **k):
    return None


def _photo_update(bot):
    msg = SimpleNamespace(photo=[SimpleNamespace(file_id="p", file_size=10)], document=None,
                          caption=None, reply_to_message=None, replies=[])

    async def _reply(text, reply_markup=None, **kwargs):
        msg.replies.append(text)
        return SimpleNamespace(delete=_noop, edit_text=_noop, message_id=1)
    msg.reply_text = _reply
    return SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=USER),
                           effective_user=SimpleNamespace(id=USER, full_name="علی"))


class _PhotoBot(_Bot):
    async def get_file(self, file_id):
        async def _dl():
            return bytearray(b"img")
        return SimpleNamespace(download_as_bytearray=_dl)


def test_photo_row_matching_an_earlier_voice_expense_is_flagged(db, monkeypatch):
    milk = _create(150_000, title="شیر")
    _fake_model(monkeypatch, RECEIPT)
    monkeypatch.setattr(messages.goals_service, "evaluate_and_alert", _noop)

    bot = _PhotoBot()
    update = _photo_update(bot)
    asyncio.run(messages.handle_photo(update, SimpleNamespace(bot=bot, user_data={})))

    summary = update.message.replies[-1]
    assert f"احتمالاً تکراریِ #{milk} «شیر»" in summary
    flagged = [t for t in bot.sent if "⚠️ احتمالاً تکراریِ" in t]
    assert len(flagged) == 1 and "۱۵۰٬۰۰۰ تومان" in flagged[0]


def test_receipt_total_matching_a_single_basket_expense_only_warns(db, monkeypatch):
    basket = _create(285_000, title="خرید سوپر")       # 2,850,000 ریال = 285,000 تومان
    _fake_model(monkeypatch, RECEIPT)

    result = asyncio.run(agent.converse_image(image=b"x", user_id=USER))

    assert len(result.created) == 3                    # فقط هشدار؛ اقلام ثبت شدند
    assert f"جمعِ این رسید با تراکنشِ #{basket} «خرید سوپر»" in result.reply
    assert all(db.get_transaction(t)["duplicate_of"] is None for t in result.created)


def test_morning_queue_flags_a_photo_row_against_a_queued_voice_expense(db, monkeypatch):
    """صفِ بعد از سهمیه: ویسِ «۶۴ نون» و اسکرین‌شاتِ همان پیامک — هر کدام دسته‌ی جدا."""
    async def _batch(**kwargs):
        return agent.AgentResult(reply="ثبت شد", created=[_create(64_000, title="نون")])
    monkeypatch.setattr(agent, "converse_batch", _batch)
    monkeypatch.setattr(pending.goals, "evaluate_and_alert", _noop)
    _fake_model(monkeypatch, {"kind": "bank_sms", "unit": "rial", "items": [
        {"line_total": 640_000, "direction": "out", "raw": "برداشت 640,000"}]})
    db.add_pending(USER, "voice", "voice-file-id")
    db.add_pending(USER, "photo", json.dumps({"file_id": "p1", "mime": "image/jpeg",
                                              "caption": ""}))

    bot = _PhotoBot()
    assert asyncio.run(pending.flush_pending(bot, USER)) is True
    assert sum("⚠️ احتمالاً تکراریِ" in t for t in bot.sent) == 1


def test_shortcut_entries_are_flagged_too(db, monkeypatch):
    from bot.services import ingest as ingest_service

    earlier = _create(64_000, title=None, source="photo")

    async def _expense_only(**kwargs):
        return agent.AgentResult(reply="", transcript="۶۴ نون", created=[_create(64_000)])
    monkeypatch.setattr(agent, "converse_expense_only", _expense_only)
    monkeypatch.setattr(ingest_service.goals_service, "evaluate_and_alert", _noop)
    assert ingest_service.repo.claim_ingest_request("req-dup-1", USER) is True

    result = asyncio.run(ingest_service._process(_Bot(), USER, "req-dup-1", "۶۴ نون", None))
    assert result.status == "recorded"
    newest = db.list_user_transactions(USER)[0]
    assert newest["duplicate_of"] == earlier


# ---------- تاریخِ واقعیِ ردیفِ عکس ----------

def test_sms_date_becomes_occurred_on():
    assert vision.occurred_on("1405/07/09") == "2026-10-01"
    assert vision.occurred_on("۱۴۰۵/۰۷/۰۸") == "2026-09-30"
    assert vision.occurred_on("05/07/09") == "2026-10-01"
    assert vision.occurred_on("1405/07") is None
    assert vision.occurred_on("1405/13/01") is None
    assert vision.occurred_on("1499/01/01") is None           # آینده
