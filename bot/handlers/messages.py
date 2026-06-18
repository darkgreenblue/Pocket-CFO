"""هندلر پیام‌های ویس و متن — مکالمه‌ی tool-using + ثبت/ویرایش تراکنش."""
from __future__ import annotations

import logging
import math

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY, EXPANDED_KEY, render_card
from bot.handlers.keyboards import BTN_ADD, BTN_REPORT, BTN_RESET, report_period_keyboard
from bot.llm import agent
from bot.llm.client import USER_FACING_UNAVAILABLE, LLMUnavailableError
from bot.services import memory
from bot.services import tags as tags_service
from bot.utils import ratelimit
from bot.utils.money import format_amount, parse_amount

logger = logging.getLogger(__name__)

LIMIT_MESSAGE = (
    "به سقف مکالمه‌ی امروزمون رسیدیم 🙏 فردا دوباره در خدمتم. "
    "(دکمه‌های ویرایش/حذف کارت‌ها و گزارش همچنان کار می‌کنند.)"
)


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and settings.is_authorized(user.id)


def _rate_limited(user_id: int) -> bool:
    return not ratelimit.allow(user_id, settings.rate_limit_max, settings.rate_limit_window)


def _reply_context(update: Update) -> str:
    """اگر کاربر به کارتِ یک تراکنش ریپلای کرده، یادداشت کانتکست برای مدل می‌سازد."""
    reply_to = update.message.reply_to_message
    if not reply_to:
        return ""
    txn = repo.find_by_card_message(update.effective_chat.id, reply_to.message_id)
    if not txn:
        return ""
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
    title = (txn.get("title") or "").strip() or "نامشخص"
    return (
        f"(کاربر به کارتِ تراکنش #{txn['id']} ریپلای کرد — عنوان: «{title}»، مبلغ: {amount}. "
        f"احتمالاً می‌خواهد همین تراکنش را با update_transaction اصلاح/تکمیل کند.)"
    )


def _threshold_notice(used_after: int) -> str | None:
    limit = settings.daily_llm_limit
    if used_after == math.ceil(limit * 0.9):
        return "📊 ظرفیت مکالمه‌ی امروزت کم‌کم داره تموم می‌شه."
    if used_after == math.ceil(limit * 0.7):
        return "📊 یادآوری: مکالمه‌ی امروزت داره به محدودیتش نزدیک می‌شه."
    return None


# ---------- ورودی‌ها ----------

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
    if text == BTN_RESET:
        user_id = update.effective_user.id
        repo.reset_user(user_id)
        ratelimit.reset(user_id)
        context.user_data.clear()
        await update.message.reply_text(
            "♻️ همه‌چیز ریست شد — تراکنش‌ها، حافظه، پروفایل و سقف امروز پاک شدند. "
            "انگار کاربر تازه‌ای 👋"
        )
        return

    awaiting = context.user_data.get(AWAITING_KEY)
    if awaiting:
        await _apply_button_edit(update, context, awaiting, text)
        return

    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return

    await _process(update, context, user_text=text, context_note=_reply_context(update))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    voice = update.message.voice
    if voice is None:
        return
    if voice.duration and voice.duration > settings.max_voice_seconds:
        await update.message.reply_text(
            f"این ویس طولانیه. لطفاً کوتاه‌تر از {settings.max_voice_seconds // 60} دقیقه بفرست."
        )
        return
    # قانون لغو: ویس وسط ویرایش → ویرایش لغو، ویس پردازش می‌شود.
    if context.user_data.pop(AWAITING_KEY, None):
        await update.message.reply_text("ویرایش قبلی لغو شد؛ این پیام صوتی جدید را پردازش می‌کنم.")
    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return
    tg_file = await voice.get_file()
    audio_bytes = bytes(await tg_file.download_as_bytearray())
    await _process(update, context, audio=audio_bytes, context_note=_reply_context(update))


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "فقط پیام صوتی (ویس) یا متن قابل قبوله. برای صدا، از دکمه‌ی میکروفون ویس بگیر."
    )


# ---------- پردازش ----------

async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                   user_text: str | None = None, audio: bytes | None = None,
                   context_note: str = "") -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    used = memory.usage_today(user_id)
    if used >= settings.daily_llm_limit:
        await update.message.reply_text(LIMIT_MESSAGE)
        return

    status_msg = await update.message.reply_text("🧠 …")
    try:
        if audio is not None:
            user_text = await agent.transcribe(audio)
            if not user_text:
                await status_msg.edit_text("صدا واضح نبود. می‌تونی دوباره و شمرده‌تر بگی؟")
                return
        result = await agent.converse(
            user_text=user_text or "",
            user_id=user_id,
            history=memory.history(user_id),
            profile=memory.profile(user_id),
            allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
            context_note=context_note,
        )
    except LLMUnavailableError:
        await status_msg.edit_text(USER_FACING_UNAVAILABLE)
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در پردازش پیام")
        await status_msg.edit_text("یه مشکلی پیش اومد. دوباره تلاش کن.")
        return

    await status_msg.delete()

    memory.remember(user_id, "user", user_text or "(ویس)")
    reply = result.reply or "باشه، انجام شد."
    notice = _threshold_notice(used + 1)
    if notice:
        reply = f"{reply}\n\n{notice}"
    await update.message.reply_text(reply)
    memory.remember(user_id, "assistant", result.reply or reply)

    shown: set[int] = set()
    for tid in result.created:
        await _send_card(context, chat_id, tid)
        shown.add(tid)
    for tid in result.updated:
        if tid not in shown:
            await _refresh_card(context, chat_id, tid)


# ---------- ویرایش دکمه‌ای ----------

async def _apply_button_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             awaiting: dict, text: str) -> None:
    txn_id = awaiting["txn_id"]
    if repo.get_transaction(txn_id) is None:
        context.user_data.pop(AWAITING_KEY, None)
        await update.message.reply_text("این تراکنش دیگر وجود ندارد.")
        return
    if awaiting["action"] == "amount":
        value = parse_amount(text)
        if value is None:
            await update.message.reply_text("عدد معتبر نبود. فقط رقم بفرست (مثلاً ۲۵۰۰۰۰).")
            return
        repo.update_transaction(txn_id, amount=value)
    else:  # title
        repo.update_transaction(txn_id, title=text.strip())
    repo.sync_status(txn_id)
    context.user_data.pop(AWAITING_KEY, None)
    await _refresh_card(context, update.effective_chat.id, txn_id)


# ---------- کارت‌ها ----------

async def _send_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_card(txn)
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    repo.set_card_message(txn_id, chat_id, msg.message_id)


async def _refresh_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int, txn_id: int,
                        expanded: bool = False) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_card(txn, expanded=expanded)
    if txn.get("card_message_id") and txn.get("card_chat_id"):
        try:
            await context.bot.edit_message_text(
                text=text, chat_id=txn["card_chat_id"],
                message_id=txn["card_message_id"], reply_markup=keyboard,
            )
            return
        except Exception:  # noqa: BLE001 — پیام قابل ویرایش نبود؛ یکی جدید بفرست
            pass
    await _send_card(context, chat_id, txn_id)
