"""هندلر پیام‌های ویس و متن — مکالمه + ثبت/ویرایش + صفِ بعد-از-ساعت‌کاری."""
from __future__ import annotations

import logging
import math

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY
from bot.handlers.cards import refresh_card, refresh_goal_card, send_card, send_goal_card
from bot.handlers.keyboards import BTN_ADD, BTN_REPORT, BTN_RESET, report_period_keyboard
from bot.llm import agent
from bot.llm.client import USER_FACING_UNAVAILABLE, LLMUnavailableError
from bot.llm.splitter import decide_parts, split_text
from bot.services import goals as goals_service
from bot.services import memory, pending
from bot.services import tags as tags_service
from bot.utils import ratelimit
from bot.utils.money import format_amount, parse_amount

TOO_LONG_MSG = "این پیام خیلی طولانیه و کامل پردازش نمی‌شه 🙏 لطفاً کوتاه‌تر و در چند پیام بفرست."
MULTIPART_REPLY = "همه رو ثبت کردم؛ کارت‌ها پایین 👇"

logger = logging.getLogger(__name__)

# پیامِ «خارج از ساعت کاری» — به‌جای ردکردن، پیام را در صف ذخیره می‌کنیم.
OFF_HOURS_MSG = (
    "😴 سهمیه‌ی مکالمه‌ی امروزمون تموم شد و من فعلاً استراحت کردم. "
    "ولی نگران نباش — هر خرجی الان بگی ذخیره می‌شه و فردا صبح همه رو یکجا برات ثبت می‌کنم. "
    "با خیال راحت ادامه بده 🙂"
)


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and settings.is_authorized(user.id)


def _rate_limited(user_id: int) -> bool:
    return not ratelimit.allow(user_id, settings.rate_limit_max, settings.rate_limit_window)


def _reply_context(update: Update) -> str:
    """اگر کاربر ریپلای زده، متنِ پیامِ ریپلای‌شده (و در صورت کارت‌بودن، شناسه) را می‌دهد."""
    r = update.message.reply_to_message
    if not r:
        return ""
    quoted = (r.text or r.caption or "").strip()
    parts = []
    if quoted:
        parts.append(f"کاربر به این پیامِ قبلی ریپلای کرد: «{quoted}»")
    txn = repo.find_by_card_message(update.effective_chat.id, r.message_id)
    if txn:
        parts.append(f"(این کارتِ تراکنش #{txn['id']} است؛ برای اصلاحش از updates استفاده کن.)")
    goal = repo.find_goal_by_card_message(update.effective_chat.id, r.message_id)
    if goal:
        parts.append(f"(این کارتِ هدف #{goal['id']} است؛ برای اصلاحش از goal_updates استفاده کن.)")
    return " ".join(parts)


def _threshold_notice(before: int, after: int) -> str | None:
    limit = settings.daily_llm_limit
    if before < math.ceil(limit * 0.9) <= after:
        return "📊 ظرفیت مکالمه‌ی امروزت کم‌کم داره تموم می‌شه."
    if before < math.ceil(limit * 0.7) <= after:
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
        uid = update.effective_user.id
        repo.reset_user(uid)
        ratelimit.reset(uid)
        context.user_data.clear()
        await update.message.reply_text(
            "♻️ همه‌چیز ریست شد — تراکنش‌ها، حافظه، پروفایل، صف و سقف امروز پاک شدند. "
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
    if context.user_data.pop(AWAITING_KEY, None):
        await update.message.reply_text("ویرایش قبلی لغو شد؛ این پیام صوتی جدید را پردازش می‌کنم.")
    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return
    await _process(update, context, audio_file_id=voice.file_id,
                   voice_duration=voice.duration or 0, context_note=_reply_context(update))


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "فقط پیام صوتی (ویس) یا متن قابل قبوله. برای صدا، از دکمه‌ی میکروفون ویس بگیر."
    )


# ---------- پردازش ----------

async def _text_pipeline(text: str, context_note: str, user_id: int) -> tuple[list, int]:
    """متن را (در صورت لزوم) افراز و هر پارت را استخراج می‌کند. (results, weight)"""
    tags = tags_service.allowed_tag_names(repo.get_tags())
    hist, prof = memory.history(user_id), memory.profile(user_id)
    n = decide_parts(text)
    if n <= 1:
        r = await agent.converse(user_text=text, user_id=user_id, history=hist,
                                 profile=prof, allowed_tags=tags, context_note=context_note)
        return [r], 1
    parts = await split_text(text, n)
    results = []
    for i, p in enumerate(parts):
        r = await agent.converse(user_text=p, user_id=user_id, history=hist, profile=prof,
                                 allowed_tags=tags, context_note=context_note if i == 0 else "")
        results.append(r)
    return results, len(parts)


async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                   user_text: str | None = None, audio_file_id: str | None = None,
                   voice_duration: int = 0, context_note: str = "") -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # اگر صفی از قبل مانده و امروز هنوز سهمیه داریم، اول صف را یکپارچه ثبت کن.
    if repo.has_pending(user_id) and memory.usage_today(user_id) < settings.daily_llm_limit:
        try:
            await pending.flush_pending(context.bot, user_id)
        except Exception:  # noqa: BLE001
            logger.exception("flush صف ناموفق بود")

    # خارج از ساعت کاری: پیام را خام ذخیره کن (ویس فقط با file_id) تا صبح یکجا ثبت شود.
    if memory.usage_today(user_id) >= settings.daily_llm_limit:
        if audio_file_id:
            repo.add_pending(user_id, "voice", audio_file_id)
        else:
            repo.add_pending(user_id, "text", user_text or "")
        await update.message.reply_text(OFF_HOURS_MSG)
        return

    used = memory.usage_today(user_id)
    status_msg = await update.message.reply_text("🧠 …")
    try:
        # ویس کوتاه → تک‌کال؛ ویس بلند → رونویسی و سپس خط لوله‌ی متن.
        if audio_file_id and voice_duration <= settings.voice_oneshot_max_seconds:
            tg_file = await context.bot.get_file(audio_file_id)
            blob = bytes(await tg_file.download_as_bytearray())
            r = await agent.converse_audio(
                audio_ogg=blob, user_id=user_id, history=memory.history(user_id),
                profile=memory.profile(user_id),
                allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
                context_note=context_note,
            )
            results, weight, user_mem = [r], 1, (r.transcript or "(ویس)")
        else:
            if audio_file_id:
                tg_file = await context.bot.get_file(audio_file_id)
                blob = bytes(await tg_file.download_as_bytearray())
                user_text = await agent.transcribe(blob)
            text = (user_text or "").strip()
            if decide_parts(text) == -1:
                await status_msg.edit_text(TOO_LONG_MSG)
                return
            results, weight = await _text_pipeline(text, context_note, user_id)
            user_mem = text or "(ویس)"
    except LLMUnavailableError:
        await status_msg.edit_text(USER_FACING_UNAVAILABLE)
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در پردازش پیام")
        await status_msg.edit_text("یه مشکلی پیش اومد. دوباره تلاش کن.")
        return

    await status_msg.delete()

    created, updated, gcreated, gupdated = [], [], [], []
    for r in results:
        created += r.created
        updated += r.updated
        gcreated += r.goals_created
        gupdated += r.goals_updated

    memory.remember(user_id, "user", user_mem, weight=weight)
    reply = MULTIPART_REPLY if weight > 1 else (results[0].reply if results else "باشه 🙂")
    notice = _threshold_notice(used, used + weight)
    if notice:
        reply = f"{reply}\n\n{notice}"
    await update.message.reply_text(reply)
    memory.remember(user_id, "assistant", reply)

    shown: set[int] = set()
    for tid in created:
        await send_card(context.bot, chat_id, tid)
        shown.add(tid)
    for tid in updated:
        if tid not in shown:
            await refresh_card(context.bot, chat_id, tid)
    gshown: set[int] = set()
    for gid in gcreated:
        await send_goal_card(context.bot, chat_id, gid)
        gshown.add(gid)
    for gid in gupdated:
        if gid not in gshown:
            await refresh_goal_card(context.bot, chat_id, gid)

    # بعد از هر تغییرِ مؤثر، هدف‌ها را ارزیابی و در صورت لزوم آلارم بده.
    if created or updated or gcreated or gupdated:
        await goals_service.evaluate_and_alert(context.bot, user_id)


# ---------- ویرایش دکمه‌ای ----------

async def _apply_button_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             awaiting: dict, text: str) -> None:
    kind = awaiting.get("kind", "txn")
    obj_id = awaiting["id"]
    chat_id = update.effective_chat.id

    if kind == "goal":
        if repo.get_goal(obj_id) is None:
            context.user_data.pop(AWAITING_KEY, None)
            await update.message.reply_text("این هدف دیگر وجود ندارد.")
            return
        if awaiting["action"] == "limit":
            value = parse_amount(text)
            if value is None:
                await update.message.reply_text("عدد معتبر نبود. فقط رقم بفرست.")
                return
            repo.update_goal(obj_id, limit_amount=value)
        else:  # topic
            goals_service.apply_update(update.effective_user.id, obj_id, {"topic": text.strip()})
        goals_service._sync_status(obj_id)
        context.user_data.pop(AWAITING_KEY, None)
        await refresh_goal_card(context.bot, chat_id, obj_id)
        await goals_service.evaluate_and_alert(context.bot, update.effective_user.id)
        return

    if repo.get_transaction(obj_id) is None:
        context.user_data.pop(AWAITING_KEY, None)
        await update.message.reply_text("این تراکنش دیگر وجود ندارد.")
        return
    if awaiting["action"] == "amount":
        value = parse_amount(text)
        if value is None:
            await update.message.reply_text("عدد معتبر نبود. فقط رقم بفرست (مثلاً ۲۵۰۰۰۰).")
            return
        repo.update_transaction(obj_id, amount=value)
    else:  # title
        repo.update_transaction(obj_id, title=text.strip())
    repo.sync_status(obj_id)
    context.user_data.pop(AWAITING_KEY, None)
    await refresh_card(context.bot, chat_id, obj_id)
    await goals_service.evaluate_and_alert(context.bot, update.effective_user.id)
