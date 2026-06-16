"""هندلر پیام‌های ویس و متن — مکالمه + استخراج تراکنش."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY, render_card
from bot.handlers.keyboards import BTN_ADD, BTN_REPORT, report_period_keyboard
from bot.llm.client import USER_FACING_UNAVAILABLE, LLMUnavailableError
from bot.llm.extract import converse_and_extract
from bot.services import memory
from bot.services import tags as tags_service
from bot.services.transactions import create_draft_from_item
from bot.utils import ratelimit
from bot.utils.money import parse_amount

logger = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and settings.is_authorized(user.id)


def _rate_limited(user_id: int) -> bool:
    return not ratelimit.allow(user_id, settings.rate_limit_max, settings.rate_limit_window)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    text = (update.message.text or "").strip()

    if text == BTN_ADD:
        await update.message.reply_text("بگو یا یک ویس بفرست چی خریدی 🙂")
        return
    if text == BTN_REPORT:
        await update.message.reply_text("کدوم بازه؟", reply_markup=report_period_keyboard())
        return

    # ورودی ویرایش یک کارت خاص؟
    awaiting = context.user_data.get(AWAITING_KEY)
    if awaiting:
        await _apply_edit(update, context, awaiting, text)
        return

    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return

    await _process_input(update, context, text=text, source="text")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return

    voice = update.message.voice
    if voice is None:  # ایمنی؛ فیلتر فقط voice را می‌فرستد
        return

    if voice.duration and voice.duration > settings.max_voice_seconds:
        await update.message.reply_text(
            f"این ویس طولانیه. لطفاً کوتاه‌تر از {settings.max_voice_seconds // 60} دقیقه بفرست."
        )
        return

    # قانون لغو: اگر وسط ویرایش یک ویس آمد، ویرایش لغو و ویس پردازش می‌شود.
    if context.user_data.pop(AWAITING_KEY, None):
        await update.message.reply_text("ویرایش قبلی لغو شد؛ این پیام صوتی جدید را پردازش می‌کنم.")

    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return

    tg_file = await voice.get_file()
    audio_bytes = bytes(await tg_file.download_as_bytearray())
    await _process_input(update, context, audio=audio_bytes, source="voice")


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "فقط پیام صوتی (ویس) یا متن قابل قبوله. برای صدا، از دکمه‌ی میکروفون ویس بگیر "
        "— فایل صوتی/تصویری رو پردازش نمی‌کنم."
    )


async def _apply_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: dict, text: str) -> None:
    txn_id = awaiting["txn_id"]
    if repo.get_transaction(txn_id) is None:
        context.user_data.pop(AWAITING_KEY, None)
        await update.message.reply_text("این تراکنش دیگر وجود ندارد.")
        return

    if awaiting["action"] == "amount":
        value = parse_amount(text)
        if value is None:
            await update.message.reply_text("عدد معتبر نبود. فقط مبلغ را به‌صورت رقم بفرست (مثلاً ۲۵۰۰۰۰).")
            return
        repo.update_transaction(txn_id, amount=value)
    else:  # title
        repo.update_transaction(txn_id, title=text)

    context.user_data.pop(AWAITING_KEY, None)
    await _send_card(update, context, txn_id)


async def _process_input(update: Update, context: ContextTypes.DEFAULT_TYPE, *, text=None, audio=None, source="text") -> None:
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("🧠 …")

    tag_bank = repo.get_tags()
    try:
        data = await converse_and_extract(
            text=text,
            audio_ogg=audio,
            history=memory.history(user_id),
            profile=memory.profile(user_id),
            allowed_tags=tags_service.allowed_tag_names(tag_bank),
        )
    except LLMUnavailableError:
        await status_msg.edit_text(USER_FACING_UNAVAILABLE)
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در پردازش پیام")
        await status_msg.edit_text("یه مشکلی پیش اومد. دوباره تلاش کن.")
        return

    await status_msg.delete()

    # ثبت در حافظه‌ی کوتاه‌مدت
    user_text = data.get("transcript") or text or "(ویس)"
    memory.remember(user_id, "user", user_text)

    reply = data.get("reply") or "باشه 🙂"
    await update.message.reply_text(reply)
    memory.remember(user_id, "assistant", reply)

    # کارت تراکنش‌ها (اگر بود) پشت‌بندِ پاسخ مکالمه
    for item in data.get("transactions", []):
        txn_id = create_draft_from_item(user_id, item, transcript=user_text, source=source)
        await _send_card(update, context, txn_id)


async def _send_card(update: Update, context: ContextTypes.DEFAULT_TYPE, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_card(txn)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=keyboard)
