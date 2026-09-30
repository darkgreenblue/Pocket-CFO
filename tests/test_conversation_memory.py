"""حافظه و زمینه‌ی گفتگو — بازسازیِ باگِ گزارش‌شده و نگهبانِ اصلاحش.

گفتگوی واقعی (۱۴۰۵/۷/۶):
  ربات (۲۲:۰۰): 🌙 … • #474 — بدون عنوان | ۸۶٬۰۰۰ تومان (نیازمند: عنوان)
  کاربر: من بهت گفته بودم این ۸۶ هزار تومن مربوط به آب معدنی از سوپرمارکت بود…
  ربات: لطفاً شماره‌ی تراکنش یا تاریخ و مبلغ را بگو…          ← زمینه گم شده
  کاربر (ریپلای روی یادآوری): خودت الان پیام دادی #474…
  ربات: باشه، متوجه شدم. الان اصلاحش می‌کنم.                 ← قولی که اجرا نشد

سه علتِ معماری بود:
  ۱) یادآوریِ شبانه در حافظه ثبت نمی‌شد → مدل نمی‌دانست چه پرسیده.
  ۲) مدل فقط متنِ گفتگو را می‌دید، نه دفتر را → «این ۸۶ هزار» به چیزی وصل نمی‌شد.
  ۳) ریپلای روی پیامِ غیرکارت به تراکنش وصل نمی‌شد، و روتر می‌توانست قولِ انجام بدهد
     بی‌آنکه کاری انجام شود — و آن قول به کاربر نشان داده می‌شد.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.handlers import callbacks, messages
from bot.llm import agent, router
from bot.services import goals, household as hh, ledger, memory, reminders
from bot.services import transactions as ts

USER, PARTNER, STRANGER = 1, 2, 3


# ---------- تلگرامِ ساختگی ----------

class _Msg:
    def __init__(self, bot, chat_id=USER, message_id=1, text="", reply_to=None):
        self._bot, self.chat_id, self.message_id = bot, chat_id, message_id
        self.text, self.caption, self.reply_to_message = text, None, reply_to
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        return await self._bot.send_message(self.chat_id, text, reply_markup=reply_markup)

    async def delete(self):
        self._bot.deleted.append(self.message_id)

    async def edit_text(self, text, **kwargs):
        self._bot.edited.append(text)


class _Bot:
    def __init__(self):
        self.counter, self.sent, self.edited, self.deleted = 100, [], [], []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.counter += 1
        msg = _Msg(self, chat_id, self.counter, text)
        msg.reply_markup = reply_markup
        self.sent.append(msg)
        return msg

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        self.edited.append(text)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


class _Ctx:
    def __init__(self, bot):
        self.bot, self.user_data = bot, {}


def _update(bot, text, reply_to=None):
    return SimpleNamespace(
        message=_Msg(bot, USER, 900, text, reply_to=reply_to),
        effective_chat=SimpleNamespace(id=USER),
        effective_user=SimpleNamespace(id=USER, full_name="علیرضا"),
    )


class _ExtractionSpy:
    """جای LLMِ استخراج: پاسخِ ثابت می‌دهد و پیام‌هایی را که دید نگه می‌دارد."""

    def __init__(self, payload):
        self.payload, self.seen = payload, None

    async def __call__(self, messages_, tools=None, json_mode=False):
        self.seen = messages_
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False),
                               tool_calls=None)

    @property
    def system(self):
        return self.seen[0]["content"]

    @property
    def conversation(self):
        return " ".join(m["content"] for m in self.seen[1:] if isinstance(m["content"], str))


def _route_returns(monkeypatch, *, record, reply=""):
    async def _fake(**kwargs):
        _fake.kwargs = kwargs
        return router.Decision(record=record, data_query=False, reply=reply)
    monkeypatch.setattr(router, "route", _fake)
    return _fake


def _untitled_draft(amount=86000):
    return ts.create_from_item(USER, {"amount": amount})


def _send_reminder(bot):
    asyncio.run(reminders.nightly_reminder(SimpleNamespace(bot=bot)))
    return bot.sent[-1]


# ---------- ۱) پیام‌های خودِ ربات در حافظه ----------

def test_nightly_reminder_is_remembered(db):
    txn_id = _untitled_draft()
    _send_reminder(_Bot())

    history = " ".join(m["content"] for m in memory.history(USER))
    assert f"#{txn_id}" in history, "یادآوری باید در حافظه باشد تا جوابش بی‌زمینه نباشد"


def test_reminder_offers_a_button_for_the_missing_field(db):
    txn_id = _untitled_draft()
    sent = _send_reminder(_Bot())

    data = [b.callback_data for row in sent.reply_markup.inline_keyboard for b in row]
    assert f"edittitle:{txn_id}" in data
    assert f"editamt:{txn_id}" not in data      # مبلغ کم نیست، دکمه‌اش هم نیست


def test_goal_alert_is_remembered_for_every_member(db):
    hh.touch(USER, "علیرضا")
    hh.accept_invite(hh.create_invite(USER, "partner", True), PARTNER, "مریم")
    goals.create_or_update_from_item(USER, {"topic": "رستوران", "limit_amount": 1000000})
    ts.create_from_item(PARTNER, {"title": "رستوران", "amount": 600000,
                                  "suggested_tags": ["رستوران"]})

    asyncio.run(goals.evaluate_and_alert(_Bot(), USER))

    for member in (USER, PARTNER):
        assert any("رستوران" in m["content"] and m["role"] == "assistant"
                   for m in memory.history(member)), member


# ---------- ۲) دفتر جلوی چشمِ مدل ----------

def test_ledger_lists_drafts_with_ids_and_what_is_missing(db):
    txn_id = _untitled_draft()
    book = ledger.snapshot(USER)

    assert f"#{txn_id}" in book
    assert "۸۶٬۰۰۰" in book
    assert "ناقص: عنوان" in book
    assert "قبلاً ثبت شده‌اند" in book            # تا مدل دوباره نسازدشان


def test_ledger_shows_goal_progress_and_open_debts(db):
    from bot.services import debts
    goals.create_or_update_from_item(USER, {"topic": "رستوران", "limit_amount": 1000000})
    ts.create_from_item(USER, {"title": "رستوران", "amount": 400000, "suggested_tags": ["رستوران"]})
    debts.create_or_update_from_item(USER, {"kind": "debt", "counterparty": "رضا",
                                            "amount": 300000})
    book = ledger.snapshot(USER)

    assert "۴۰٪" in book and "مانده ۶۰۰٬۰۰۰" in book
    assert "رضا" in book and "۳۰۰٬۰۰۰" in book


def test_empty_ledger_adds_nothing_to_the_prompt(db):
    assert ledger.snapshot(USER) == ""


# ---------- ۳) ریپلای روی پیامی که به تراکنش اشاره می‌کند ----------

def test_ids_in_a_quoted_bot_message_resolve_to_household_transactions(db):
    mine = _untitled_draft()
    theirs = ts.create_from_item(STRANGER, {"title": "مالِ غریبه", "amount": 1})

    found = messages.referenced_transactions(f"• #{mine} … • #{theirs} …", USER)

    assert [t["id"] for t in found] == [mine]    # تراکنشِ خانوارِ دیگر وصل نمی‌شود


def test_persian_digits_after_hash_also_resolve(db):
    txn_id = _untitled_draft()
    persian = str(txn_id).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert [t["id"] for t in messages.referenced_transactions(f"#{persian}", USER)] == [txn_id]


def test_reply_to_the_reminder_is_linked_to_the_transaction(db):
    txn_id = _untitled_draft()
    bot = _Bot()
    reminder = _send_reminder(bot)
    update = _update(bot, "خودت پرسیدی، آب معدنی بود", reply_to=reminder)

    note, refers = messages._reply_context(update)

    assert refers is True
    assert f"#{txn_id}" in note and "updates" in note


# ---------- ۴) بازپخشِ کاملِ گفتگوی گزارش‌شده ----------

def test_unreplied_followup_sees_the_reminder_and_the_ledger_and_fixes_it(db, monkeypatch):
    """پیامِ اولِ کاربر (بدونِ ریپلای) — حالا مدل هم یادآوری را می‌بیند هم دفتر را."""
    txn_id = _untitled_draft()
    bot = _Bot()
    _send_reminder(bot)
    _route_returns(monkeypatch, record=True)
    spy = _ExtractionSpy({"reply": "", "updates": [{"transaction_id": txn_id,
                                                    "title": "آب معدنی"}]})
    monkeypatch.setattr(agent, "chat", spy)

    update = _update(bot, "من بهت گفته بودم این ۸۶ هزار تومن مربوط به آب معدنی بود")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    assert f"#{txn_id}" in spy.conversation, "یادآوری باید در تاریخچه‌ی مدل باشد"
    assert f"#{txn_id}" in spy.system, "دفتر باید در پرامپتِ مدل باشد"
    assert db.get_transaction(txn_id)["title"] == "آب معدنی"
    said = [t for t, _ in update.message.replies]
    assert any("اصلاح شد" in t and "آب معدنی" in t for t in said), said


def test_reply_to_reminder_runs_the_edit_even_if_the_router_only_chats(db, monkeypatch):
    """پیامِ دومِ کاربر: روتر قول داد و کاری نکرد. حالا ریپلای روی یادآوری مستقیم ثبت است."""
    txn_id = _untitled_draft()
    bot = _Bot()
    reminder = _send_reminder(bot)
    _route_returns(monkeypatch, record=False, reply="باشه، متوجه شدم. الان اصلاحش می‌کنم.")
    spy = _ExtractionSpy({"reply": "", "updates": [{"transaction_id": txn_id,
                                                    "title": "آب معدنی"}]})
    monkeypatch.setattr(agent, "chat", spy)

    update = _update(bot, "خودت الان پیام دادی ۴۷۴ بدون عنوان، دارم تأیید می‌کنم",
                     reply_to=reminder)
    note, refers = messages._reply_context(update)
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text,
                                  context_note=note, force_record=refers))

    assert spy.seen is not None, "استخراج باید اجرا شود، نه جوابِ گفتگوییِ روتر"
    assert db.get_transaction(txn_id)["title"] == "آب معدنی"
    said = " ".join(t for t, _ in update.message.replies)
    assert "الان اصلاحش می‌کنم" not in said


# ---------- ۵) بازخوردِ راستگو ----------

def test_router_promise_triggers_the_real_edit_instead_of_being_shown(db, monkeypatch):
    """روتر قول داد ولی record نزد → آن قول نشانه‌ی نیت است؛ کار واقعاً اجرا می‌شود."""
    txn_id = _untitled_draft()
    _route_returns(monkeypatch, record=False, reply="باشه، الان اصلاحش می‌کنم.")
    spy = _ExtractionSpy({"reply": "", "updates": [{"transaction_id": txn_id,
                                                    "title": "آب معدنی"}]})
    monkeypatch.setattr(agent, "chat", spy)
    bot = _Bot()
    update = _update(bot, "عنوانِ اون ۸۶ تومنی رو بذار آب معدنی")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    assert spy.seen is not None
    assert db.get_transaction(txn_id)["title"] == "آب معدنی"
    said = " ".join(t for t, _ in update.message.replies)
    assert "اصلاحش می‌کنم" not in said and "اصلاح شد" in said


def test_router_promise_with_nothing_to_do_ends_honestly(db, monkeypatch):
    _route_returns(monkeypatch, record=False, reply="باشه، الان اصلاحش می‌کنم.")
    monkeypatch.setattr(agent, "chat", _ExtractionSpy({"reply": "", "updates": []}))
    bot = _Bot()
    update = _update(bot, "اونو درستش کن")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    said = " ".join(t for t, _ in update.message.replies)
    assert "اصلاحش می‌کنم" not in said
    assert "چیزی ثبت یا اصلاح نشد" in said


def test_nothing_changed_means_no_fake_success(db, monkeypatch):
    """استخراج اجرا شد ولی هیچ رکوردی عوض نشد → «ثبت شد ✅» دروغ است."""
    _route_returns(monkeypatch, record=True)
    monkeypatch.setattr(agent, "chat", _ExtractionSpy({"reply": "اصلاح شد!", "updates": []}))
    bot = _Bot()
    update = _update(bot, "اونو درستش کن")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    said = " ".join(t for t, _ in update.message.replies)
    assert "ثبت شد ✅" not in said and "اصلاح شد!" not in said
    assert "چیزی ثبت یا اصلاح نشد" in said


def test_a_clarifying_question_from_the_model_is_kept(db, monkeypatch):
    _route_returns(monkeypatch, record=True)
    monkeypatch.setattr(agent, "chat", _ExtractionSpy(
        {"reply": "کدوم ۸۶ تومنی؟ دیروز یا امروز؟", "updates": []}))
    bot = _Bot()
    update = _update(bot, "اون ۸۶ تومنی آب معدنی بود")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    assert any("کدوم ۸۶ تومنی" in t for t, _ in update.message.replies)


@pytest.mark.parametrize("text, claims", [
    ("باشه، الان اصلاحش می‌کنم.", True),
    ("ثبت شد ✅", True),
    ("حذف شد", True),
    ("انجامش می‌دم", True),
    ("کدوم یکی؟ دیروزی یا امروزی؟", False),
    ("سلام! چطوری؟", False),
    ("امروز دو تا خرج ثبت شده، جمعاً ۳۰۰ تومن", False),   # توصیف، نه قول
    ("این هدف قبلاً انجام شده بود", False),
])
def test_action_claims_are_detected(text, claims):
    assert messages.claims_action(text) is claims


# ---------- ۶) حذف با حرف‌زدن — فقط با تأیید ----------

def test_spoken_delete_asks_first_and_deletes_nothing(db, monkeypatch):
    txn_id = ts.create_from_item(USER, {"title": "تکراری", "amount": 1000})
    _route_returns(monkeypatch, record=True)
    monkeypatch.setattr(agent, "chat", _ExtractionSpy(
        {"reply": "", "deletes": [{"transaction_id": txn_id}]}))
    bot = _Bot()
    update = _update(bot, "اون تکراریه، پاکش کن")
    asyncio.run(messages._process(update, _Ctx(bot), user_text=update.message.text))

    assert db.get_transaction(txn_id) is not None       # هنوز حذف نشده
    question = [(t, kb) for t, kb in update.message.replies if "حذف کنم" in t]
    assert question, update.message.replies
    data = [b.callback_data for row in question[0][1].inline_keyboard for b in row]
    assert data == [f"delete:{txn_id}", f"keep:{txn_id}"]


def test_delete_request_for_another_households_transaction_is_ignored(db, monkeypatch):
    theirs = ts.create_from_item(STRANGER, {"title": "مالِ غریبه", "amount": 1})
    monkeypatch.setattr(agent, "chat", _ExtractionSpy(
        {"reply": "", "deletes": [{"transaction_id": theirs}]}))
    result = asyncio.run(agent.converse(user_text="پاکش کن", user_id=USER, allowed_tags=[]))
    assert result.deletion_requests == []


def test_confirmed_delete_also_marks_the_original_card(db):
    txn_id = ts.create_from_item(USER, {"title": "تکراری", "amount": 1000})
    db.set_card_message(txn_id, USER, 55)                 # کارتِ اصلی جای دیگری است
    bot = _Bot()
    query = SimpleNamespace(data=f"delete:{txn_id}", message=_Msg(bot, USER, 77),
                            answers=[])

    async def answer(text="", show_alert=False):
        query.answers.append(text)

    async def edit(text, reply_markup=None):
        bot.edited.append(text)

    query.answer, query.edit_message_text = answer, edit
    update = SimpleNamespace(callback_query=query,
                             effective_user=SimpleNamespace(id=USER))
    asyncio.run(callbacks.on_callback(update, _Ctx(bot)))

    assert db.get_transaction(txn_id) is None
    assert bot.edited.count("🗑 حذف شد.") == 2          # پیامِ تأیید + کارتِ اصلی


# ---------- ۷) عنوان از حرفِ خودِ کاربر ----------

def test_title_comes_from_items_when_the_model_leaves_it_empty(db):
    """ریشه‌ی خودِ #474: اقلام ثبت شده بود ولی عنوان نه."""
    txn_id = ts.create_from_item(USER, {"amount": 86000,
                                        "mentioned_items": ["آب معدنی", "آب گازدار"]})
    txn = db.get_transaction(txn_id)
    assert txn["title"] == "آب معدنی، آب گازدار"
    assert txn["status"] == "confirmed"                 # دیگر ناقص نیست، یادآوری هم نمی‌آید


def test_no_items_still_means_no_invented_title(db):
    assert db.get_transaction(ts.create_from_item(USER, {"amount": 150000}))["title"] is None


# ---------- ۸) ابزارِ اهداف برای سؤال‌های دیتایی ----------

def test_goals_tool_reports_spent_and_remaining(db):
    from bot.llm.tools import dispatch
    goals.create_or_update_from_item(USER, {"topic": "رستوران", "limit_amount": 1000000})
    ts.create_from_item(USER, {"title": "رستوران", "amount": 250000, "suggested_tags": ["رستوران"]})

    out = dispatch("get_goals", {}, user_id=USER)
    goal = out["goals"][0]
    assert (goal["spent_toman"], goal["remaining_toman"], goal["percent_used"]) == \
        (250000, 750000, 25)
