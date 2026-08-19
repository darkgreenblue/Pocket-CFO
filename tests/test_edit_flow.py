"""جریانِ ویرایشِ دکمه‌ای: انصراف، تأییدِ صریح، و پاک‌شدنِ پیام‌های موقت.

سه شکایتِ واقعیِ کاربر که این تست‌ها نگهبانشان‌اند:
  ۱) راهی برای انصراف از ویرایش نبود.
  ۲) بعد از ویرایشِ موفق هیچ تأییدی نمی‌آمد؛ کارت بی‌صدا عوض می‌شد.
  ۳) پیام‌های موقتِ ویرایش در گفتگو می‌ماندند.
"""
import asyncio

import pytest

from bot.handlers import callbacks, messages
from bot.flows.draft_flow import AWAITING_KEY
from bot.services import transactions as ts

USER = 1


class _Msg:
    """پیامِ ساختگی که می‌داند به چه چیزی جواب داده و چه چیزی فرستاده."""

    def __init__(self, chat_id=USER, message_id=100, text="", bot=None):
        self.chat_id = chat_id
        self.message_id = message_id
        self.text = text
        self._bot = bot
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        self._bot.counter += 1
        sent = _Msg(self.chat_id, self._bot.counter, text, self._bot)
        self._bot.sent.append(sent)
        return sent


class _Bot:
    def __init__(self):
        self.counter = 500
        self.sent: list[_Msg] = []
        self.deleted: list[tuple[int, int]] = []
        self.edited: list[str] = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        self.edited.append(text)

    async def send_message(self, chat_id, text, reply_markup=None):
        self.counter += 1
        sent = _Msg(chat_id, self.counter, text, self)
        self.sent.append(sent)
        return sent


class _Query:
    def __init__(self, data, bot):
        self.data = data
        self.message = _Msg(bot=bot)
        self.answers: list[str] = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        pass


class _Ctx:
    def __init__(self, bot):
        self.bot = bot
        self.user_data: dict = {}


class _Update:
    def __init__(self, bot, text=""):
        self.message = _Msg(text=text, bot=bot)
        self.effective_chat = type("C", (), {"id": USER})()
        self.effective_user = type("U", (), {"id": USER, "full_name": "علی"})()


@pytest.fixture
def txn(db):
    return ts.create_from_item(USER, {"title": "نان", "amount": 200000})


def _start_amount_edit(bot, ctx, txn_id):
    query = _Query(f"editamt:{txn_id}", bot)
    asyncio.run(callbacks.start_edit(query, ctx, kind="txn", action="amount",
                                     obj_id=txn_id, prompt="مبلغ را بفرست"))
    return query


def test_edit_prompt_offers_a_cancel_button(db, txn):
    bot, ctx = _Bot(), _Ctx(_Bot())
    ctx.bot = bot
    query = _start_amount_edit(bot, ctx, txn)

    prompt_text, markup = query.message.replies[0]
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "editcancel:0" in buttons
    assert ctx.user_data[AWAITING_KEY]["id"] == txn


def test_cancel_clears_state_and_removes_the_prompt(db, txn):
    bot, ctx = _Bot(), _Ctx(None)
    ctx.bot = bot
    _start_amount_edit(bot, ctx, txn)
    prompt_id = ctx.user_data[AWAITING_KEY]["cleanup"][0]

    cancel = _Query("editcancel:0", bot)
    update = _Update(bot)
    update.callback_query = cancel
    asyncio.run(callbacks.on_callback(update, ctx))

    assert AWAITING_KEY not in ctx.user_data          # ویرایش واقعاً تمام شد
    assert (USER, prompt_id) in bot.deleted           # پرسش از گفتگو پاک شد
    assert db.get_transaction(txn)["amount"] == 200000   # هیچ‌چیز تغییر نکرد


def test_successful_edit_confirms_and_cleans_up(db, txn):
    bot, ctx = _Bot(), _Ctx(None)
    ctx.bot = bot
    _start_amount_edit(bot, ctx, txn)
    prompt_id = ctx.user_data[AWAITING_KEY]["cleanup"][0]

    update = _Update(bot, text="۳۵۰۰۰۰")
    asyncio.run(messages._apply_button_edit(update, ctx, ctx.user_data[AWAITING_KEY], "۳۵۰۰۰۰"))

    assert db.get_transaction(txn)["amount"] == 350000
    confirmations = [t for t, _ in update.message.replies]
    assert any(t.startswith("✅") and "۳۵۰٬۰۰۰" in t for t in confirmations), confirmations
    assert (USER, prompt_id) in bot.deleted
    assert AWAITING_KEY not in ctx.user_data


def test_decimal_edit_keeps_the_fraction(db):
    """باگِ گزارش‌شده: ۱.۴ دلار موقع ویرایش ۱۴ دلار می‌شد."""
    txn_id = ts.create_from_item(USER, {"title": "اشتراک", "amount": 1.5, "currency": "usd"})
    bot, ctx = _Bot(), _Ctx(None)
    ctx.bot = bot
    _start_amount_edit(bot, ctx, txn_id)

    update = _Update(bot, text="1.4")
    asyncio.run(messages._apply_button_edit(update, ctx, ctx.user_data[AWAITING_KEY], "1.4"))

    assert db.get_transaction(txn_id)["amount"] == pytest.approx(1.4)
    assert any("دلار" in t for t, _ in update.message.replies)


def test_invalid_number_keeps_the_edit_open_with_cancel(db, txn):
    bot, ctx = _Bot(), _Ctx(None)
    ctx.bot = bot
    _start_amount_edit(bot, ctx, txn)

    update = _Update(bot, text="سلام")
    asyncio.run(messages._apply_button_edit(update, ctx, ctx.user_data[AWAITING_KEY], "سلام"))

    # هنوز منتظر ورودیِ درست است، ولی راهِ خروج هم دارد…
    assert AWAITING_KEY in ctx.user_data
    _, markup = update.message.replies[-1]
    assert "editcancel:0" in [b.callback_data for row in markup.inline_keyboard for b in row]
    # …و خطا هم برای پاک‌سازیِ بعدی ثبت شده تا در گفتگو نماند.
    assert len(ctx.user_data[AWAITING_KEY]["cleanup"]) == 2
    assert db.get_transaction(txn)["amount"] == 200000
