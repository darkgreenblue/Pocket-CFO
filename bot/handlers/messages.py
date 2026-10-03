"""هندلر پیام‌های ویس و متن — مکالمه + ثبت/ویرایش + صفِ بعد-از-ساعت‌کاری."""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY
from bot.handlers.cards import (
    refresh_card,
    refresh_debt_card,
    refresh_goal_card,
    send_card,
    send_debt_card,
    send_goal_card,
)
from bot.handlers.callbacks import clear_edit_messages
from bot.handlers.keyboards import (
    BTN_ADD,
    BTN_DEBT,
    BTN_GOAL,
    BTN_HOUSEHOLD,
    BTN_REPORT,
    cancel_keyboard,
    clarify_keyboard,
    delete_confirm_keyboard,
    relation_keyboard,
    report_period_keyboard,
)
from bot.llm import agent, router
from bot.llm.client import USER_FACING_UNAVAILABLE, LLMUnavailableError
from bot.llm.splitter import decide_parts, split_text
from bot.services import clarify as clarify_service
from bot.services import debts as debts_service
from bot.services import duplicates as duplicates_service
from bot.services import goals as goals_service
from bot.services import household as household_service
from bot.services import ledger, memory, pending
from bot.services import tags as tags_service
from bot.utils import ratelimit
from bot.utils.money import format_amount, normalize_digits, parse_amount, to_persian_digits

TOO_LONG_MSG = "این پیام خیلی طولانیه و کامل پردازش نمی‌شه 🙏 لطفاً کوتاه‌تر و در چند پیام بفرست."
MULTIPART_REPLY = "همه رو ثبت کردم؛ کارت‌ها پایین 👇"
RECORD_REPLY = "ثبت شد ✅"
FALLBACK_REPLY = "متوجه نشدم دقیقاً چی می‌خوای 🙂 می‌تونی یه خرج بگی، گزارش بخوای، یا سؤال مالی بپرسی."

# وقتی کاربر از دکمه‌ی «ثبت بدهی/طلب» یا «ثبت هدف» آمده، نیتش را می‌دانیم. این راهنما
# یک‌بارمصرف است و به پیامِ بعدی چسبانده می‌شود تا تشخیصِ نیت شانسی نباشد.
INTENT_HINT_KEY = "intent_hint"
INTENT_HINTS = {
    "debt": "(کاربر دکمه‌ی «ثبت بدهی/طلب» را زده، پس نیتش ثبتِ بدهی یا طلب است.)",
    "goal": "(کاربر دکمه‌ی «ثبت هدف» را زده، پس نیتش تعیینِ سقف/هدفِ بودجه است.)",
}

logger = logging.getLogger(__name__)

# پیامِ «خارج از ساعت کاری» — به‌جای ردکردن، پیام را در صف ذخیره می‌کنیم.
OFF_HOURS_MSG = (
    "😴 سهمیه‌ی مکالمه‌ی امروزمون تموم شد و من فعلاً استراحت کردم. "
    "ولی نگران نباش — هر خرجی الان بگی ذخیره می‌شه و فردا صبح همه رو یکجا برات ثبت می‌کنم. "
    "با خیال راحت ادامه بده 🙂"
)


def _authorized(update: Update) -> bool:
    """مجاز است اگر خودش در تنظیمات باشد، یا با لینکِ دعوت عضو خانوار شده باشد."""
    user = update.effective_user
    if not user or not household_service.authorized(user.id):
        return False
    # نامِ نمایشی را تازه نگه می‌داریم تا گزارشِ «چه کسی ثبت کرده» درست باشد.
    household_service.touch(user.id, (getattr(user, "full_name", None) or "").strip())
    return True


def _rate_limited(user_id: int) -> bool:
    return not ratelimit.allow(user_id, settings.rate_limit_max, settings.rate_limit_window)


_TXN_REF = re.compile(r"#(\d{1,9})")


def referenced_transactions(quoted: str, user_id: int) -> list[dict]:
    """تراکنش‌هایی که یک پیامِ ربات با «#شماره» از آن‌ها نام برده (مثلِ یادآوریِ شبانه).

    فقط تراکنش‌های واقعیِ خانوارِ همین کاربر برمی‌گردند؛ عددِ تصادفیِ بعد از # چیزی را
    به کسِ دیگری وصل نمی‌کند.
    """
    household = repo.household_id_for(user_id)
    found: list[dict] = []
    for raw in dict.fromkeys(_TXN_REF.findall(normalize_digits(quoted or ""))):
        txn = repo.get_transaction(int(raw))
        if txn and txn.get("household_id") == household:
            found.append(txn)
    return found


def _reply_context(update: Update) -> tuple[str, bool]:
    """یادداشتِ ریپلای برای مدل، و اینکه آیا ریپلای به یک رکوردِ مشخص اشاره می‌کند.

    قبلاً فقط ریپلای روی **کارت** به تراکنش وصل می‌شد. ریپلای روی یادآوریِ شبانه — که
    خودش «#474» را نام برده — فقط متنِ خام می‌رساند، و مدل نمی‌فهمید کدام تراکنش است.
    """
    r = update.message.reply_to_message
    if not r:
        return "", False
    quoted = (r.text or r.caption or "").strip()
    parts = []
    refers_to_record = False
    if quoted:
        parts.append(f"کاربر به این پیامِ قبلی ریپلای کرد: «{quoted}»")
    txn = repo.find_by_card_message(update.effective_chat.id, r.message_id)
    if txn:
        refers_to_record = True
        parts.append(f"(این کارتِ تراکنش #{txn['id']} است؛ برای اصلاحش از updates استفاده کن.)")
    else:
        mentioned = referenced_transactions(quoted, update.effective_user.id)
        if mentioned:
            refers_to_record = True
            ids = "، ".join(f"#{t['id']}" for t in mentioned)
            parts.append(
                f"(این پیام به تراکنش‌های {ids} اشاره می‌کند. حرفِ کاربر درباره‌ی همین‌هاست: "
                "برای تکمیل/اصلاح از updates با همین transaction_idها استفاده کن و تراکنشِ "
                "تازه نساز. اگر معلوم نیست کدام، در reply بپرس.)"
            )
    goal = repo.find_goal_by_card_message(update.effective_chat.id, r.message_id)
    if goal:
        refers_to_record = True
        parts.append(f"(این کارتِ هدف #{goal['id']} است؛ برای اصلاحش از goal_updates استفاده کن.)")
    debt = repo.find_debt_by_card_message(update.effective_chat.id, r.message_id)
    if debt:
        refers_to_record = True
        parts.append(f"(این کارتِ بدهی/طلب #{debt['id']} است؛ برای اصلاح یا تسویه‌اش از "
                     "debt_updates استفاده کن.)")
    return " ".join(parts), refers_to_record


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
    if text == BTN_DEBT:
        context.user_data[INTENT_HINT_KEY] = "debt"
        await update.message.reply_text(
            "بگو به کی چقدر بدهکاری، یا از کی چقدر طلبکاری 🤝\n"
            "مثلاً: «۵۰۰ به رضا قرض دادم» یا «۲ میلیون از بابا گرفتم».\n"
            "یادت باشه بدهی/طلب به اسمِ یک شخص ثبت می‌شه؛ قسط و پرداختِ سرویس‌ها خرج حساب می‌شن."
        )
        return
    if text == BTN_GOAL:
        context.user_data[INTENT_HINT_KEY] = "goal"
        await update.message.reply_text(
            "بگو برای چه دسته‌ای چه سقفی می‌خوای 🎯\n"
            "مثلاً: «این ماه بیشتر از ۳ میلیون رستوران نرم»."
        )
        return
    if text == BTN_HOUSEHOLD:
        await update.message.reply_text(
            "می‌خوای چه کسی رو به خانوارت اضافه کنی؟ اول نسبتش رو انتخاب کن:",
            reply_markup=relation_keyboard(),
        )
        return
    awaiting = context.user_data.get(AWAITING_KEY)
    if awaiting:
        await _apply_button_edit(update, context, awaiting, text)
        return

    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return

    note, refers_to_record = _reply_context(update)
    await _process(update, context, user_text=text, context_note=note,
                   force_record=refers_to_record)


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
    awaiting = context.user_data.pop(AWAITING_KEY, None)
    if awaiting:
        await clear_edit_messages(context.bot, awaiting)
        await update.message.reply_text("ویرایش قبلی لغو شد؛ این پیام صوتی جدید را پردازش می‌کنم.")
    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return
    note, refers_to_record = _reply_context(update)
    await _process(update, context, audio_file_id=voice.file_id,
                   voice_duration=voice.duration or 0, context_note=note,
                   force_record=refers_to_record)


UNSUPPORTED_MSG = (
    "ویس، متن یا عکس قابل قبوله 🙂 برای صدا از دکمه‌ی میکروفون ویس بگیر؛ برای ثبت از روی "
    "رسید یا پیامک‌های بانکی، عکس/اسکرین‌شاتش رو بفرست."
)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(UNSUPPORTED_MSG)


# ---------- عکس (رسید، پیامکِ بانکی، موبایل‌بانک) ----------

# فرمت‌هایی که مدل مستقیم می‌خواند. HEIC (عکسِ پیش‌فرضِ آیفون وقتی «فایل» فرستاده شود)
# عمداً نیست: پشتیبانی‌اش در OpenRouter تضمینی نیست؛ کاربر می‌تواند همان را «عکس» بفرستد.
IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
PHOTO_STATUS = "🖼 دارم عکس رو می‌خونم…"
IMAGE_TOO_BIG = "این فایل خیلی بزرگه 🙏 لطفاً به‌صورت «عکس» بفرست، نه فایل."
IMAGE_FORMAT_MSG = ("این فرمتِ عکس رو نمی‌تونم بخونم. لطفاً به‌صورت «عکس» بفرست "
                    "(یا فایلِ JPG/PNG).")


def photo_payload(message) -> Optional[tuple[str, str, int]]:
    """(file_id، نوعِ فایل، حجم) — عکسِ معمولی یا عکسی که به‌صورت «فایل» آمده؛ وگرنه None.

    از عکسِ معمولی بزرگ‌ترین اندازه را برمی‌داریم: برای خواندنِ ردیف‌های ریزِ رسید، کیفیت
    از هر چیزی مهم‌تر است.
    """
    if message.photo:
        best = message.photo[-1]
        return best.file_id, "image/jpeg", best.file_size or 0
    doc = message.document
    if doc is not None and (doc.mime_type or "").lower() in IMAGE_MIMES:
        mime = "image/jpeg" if doc.mime_type.lower() == "image/jpg" else doc.mime_type.lower()
        return doc.file_id, mime, doc.file_size or 0
    return None


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    payload = photo_payload(update.message)
    if payload is None:
        await update.message.reply_text(IMAGE_FORMAT_MSG)
        return
    file_id, mime, size = payload
    if size and size > settings.max_image_bytes:
        await update.message.reply_text(IMAGE_TOO_BIG)
        return
    awaiting = context.user_data.pop(AWAITING_KEY, None)
    if awaiting:
        await clear_edit_messages(context.bot, awaiting)
        await update.message.reply_text("ویرایش قبلی لغو شد؛ این عکس رو پردازش می‌کنم.")
    # راهنمای نیتِ دکمه‌ها (بدهی/هدف) به عکس ربطی ندارد؛ نباید به پیامِ بعدی نشت کند.
    context.user_data.pop(INTENT_HINT_KEY, None)
    if _rate_limited(update.effective_user.id):
        await update.message.reply_text("یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست.")
        return
    caption = (update.message.caption or "").strip()
    await _process_photo(update, context, file_id=file_id, mime=mime, caption=caption)


async def download_image(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def _process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                         file_id: str, mime: str, caption: str) -> None:
    user_id = update.effective_user.id

    if repo.has_pending(user_id) and memory.usage_today(user_id) < settings.daily_llm_limit:
        try:
            await pending.flush_pending(context.bot, user_id)
        except Exception:  # noqa: BLE001
            logger.exception("flush صف ناموفق بود")

    # بعد از پایانِ سهمیه، عکس هم مثل ویس گم نمی‌شود: file_id در صف می‌ماند تا صبح.
    if memory.usage_today(user_id) >= settings.daily_llm_limit:
        repo.add_pending(user_id, "photo", json.dumps(
            {"file_id": file_id, "mime": mime, "caption": caption}, ensure_ascii=False))
        await update.message.reply_text(OFF_HOURS_MSG)
        return

    used = memory.usage_today(user_id)
    status_msg = await update.message.reply_text(PHOTO_STATUS)
    try:
        blob = await download_image(context.bot, file_id)
        if len(blob) > settings.max_image_bytes:
            await status_msg.edit_text(IMAGE_TOO_BIG)
            return
        result = await agent.converse_image(
            image=blob, mime=mime, caption=caption, user_id=user_id,
            allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
        )
    except LLMUnavailableError:
        await status_msg.edit_text(USER_FACING_UNAVAILABLE)
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در پردازشِ عکس (user=%s)", user_id)
        await status_msg.edit_text("یه مشکلی در خوندنِ عکس پیش اومد. دوباره تلاش کن.")
        return

    await status_msg.delete()
    await _emit(update, context, results=[result], weight=1,
                user_mem=result.transcript or "[عکس]", used=used, data_answer="")


# ---------- پردازش ----------

async def _text_pipeline(text: str, context_note: str, user_id: int,
                         book: str = "") -> tuple[list, int]:
    """متن را (در صورت لزوم) افراز و هر پارت را استخراج می‌کند. (results, weight)"""
    tags = tags_service.allowed_tag_names(repo.get_tags())
    hist, prof = memory.history(user_id), memory.profile(user_id)
    n = decide_parts(text)
    if n <= 1:
        r = await agent.converse(user_text=text, user_id=user_id, history=hist,
                                 profile=prof, allowed_tags=tags, context_note=context_note,
                                 ledger=book)
        return [r], 1
    parts = await split_text(text, n)
    results = []
    for i, p in enumerate(parts):
        r = await agent.converse(user_text=p, user_id=user_id, history=hist, profile=prof,
                                 allowed_tags=tags, context_note=context_note if i == 0 else "",
                                 ledger=book)
        results.append(r)
    return results, len(parts)


# ادعای انجامِ کار: «ثبت شد»، «اصلاحش می‌کنم»، «حذف کردم». عمداً «ثبت شده» (توصیفِ وضعیت،
# مثل «امروز دو خرج ثبت شده») را نمی‌گیرد — آن قول نیست، گزارش است.
_ACTION_CLAIM = re.compile(
    r"(ثبت\s*(شد(?!ه)|کردم|ش\s*کردم|ش\s*می)|اصلاح\s*(شد(?!ه)|کردم|ش\s*می|ش\s*کردم)|"
    r"درست\s*(ش\s*)?(شد(?!ه)|کردم|می)|حذف\s*(شد(?!ه)|کردم|ش\s*می)|"
    r"انجام\s*(شد(?!ه)|ش\s*می|دادم)|به‌روز\s*شد(?!ه)|تغییر\s*(دادم|کرد(?!ه)))"
)

NOTHING_DONE = (
    "🤔 چیزی ثبت یا اصلاح نشد — مطمئن نشدم منظورت کدوم مورده.\n"
    "روی کارتش ریپلای بزن، یا بگو کدوم (مثلاً «همون ۸۶ تومنیِ دیشب»)."
)


def claims_action(text: str) -> bool:
    """آیا متن ادعا می‌کند کاری انجام شده/می‌شود؟ (برای جلوگیری از قولِ بی‌پشتوانه)"""
    return bool(_ACTION_CLAIM.search(text or ""))


def _update_lines(updated: list[int], dupdated: list[int], gupdated: list[int],
                  skip: set[int]) -> list[str]:
    """خلاصه‌ی تغییراتِ واقعی — از دیتابیس، نه از حرفِ مدل.

    کارتِ اصلاح‌شده سرِ جای خودش (شاید خیلی بالاتر در گفتگو) ویرایش می‌شود و کاربر
    نمی‌بیندش؛ این خط همین‌جا می‌گوید دقیقاً چه چیزی عوض شد.
    """
    lines = []
    for tid in updated:
        if tid in skip:
            continue
        txn = repo.get_transaction(tid)
        if not txn:
            continue
        title = (txn.get("title") or "").strip() or "(بی‌عنوان)"
        line = f"✏️ اصلاح شد: {title} — {format_amount(txn.get('amount'), txn.get('currency_display', 'toman'))}"
        missing = [label for label, gap in (("عنوان", not (txn.get("title") or "").strip()),
                                            ("مبلغ", txn.get("amount") is None)) if gap]
        if missing:
            line += f" (هنوز ناقص: {' و '.join(missing)})"
        lines.append(line)
    for did in dupdated:
        debt = repo.get_debt(did)
        if debt:
            rest = debts_service.remaining(debt)
            lines.append(f"✏️ {debts_service.KIND_LABELS.get(debt.get('kind'), 'بدهی')} "
                         f"{debt.get('counterparty') or ''} به‌روز شد — مانده "
                         f"{format_amount(rest, debt.get('currency_display', 'toman'))}")
    for gid in gupdated:
        goal = repo.get_goal(gid)
        if goal:
            lines.append(f"🎯 هدفِ «{goal.get('topic') or '—'}» به‌روز شد")
    return lines


def duplicate_summary(found: dict[int, int]) -> str:
    """یک خط برای پیامِ پاسخ؛ جزئیاتِ هر مورد روی کارتِ خودش است."""
    if not found:
        return ""
    if len(found) == 1:
        original = repo.get_transaction(next(iter(found.values())))
        if original:
            return (f"⚠️ احتمالاً تکراریِ {duplicates_service.describe(original)} — "
                    "اگه همونه، روی کارت «🗑 تکراریه، حذف» رو بزن.")
    return (f"⚠️ {to_persian_digits(len(found))} مورد احتمالاً تکراری‌اند (با تراکنش‌های قبلی "
            "جور درمیان)؛ روی کارتشون مشخص شده — اگه واقعاً تکراری‌ان، «🗑 تکراریه، حذف».")


async def _ask(update: Update, user_id: int, text: str, keyboard) -> None:
    """سؤالی که ربات از کاربر می‌پرسد — در حافظه هم می‌ماند تا جوابش بی‌زمینه نباشد."""
    await update.message.reply_text(text, reply_markup=keyboard)
    memory.remember_bot(user_id, text)


async def _emit(update: Update, context: ContextTypes.DEFAULT_TYPE, *, results: list,
                weight: int, user_mem: str, used: int, data_answer: str) -> None:
    """نتیجه‌ی یک ثبت را می‌فرستد: متنِ پاسخ + کارت‌ها + ارزیابیِ اهداف.

    متنِ پاسخ از **آنچه واقعاً اتفاق افتاد** ساخته می‌شود، نه از حرفِ مدل. قبلاً اگر
    هیچ‌چیز ثبت یا اصلاح نمی‌شد، باز «ثبت شد ✅» یا قولِ روتر («الان اصلاحش می‌کنم») نشان
    داده می‌شد — کاربر فکر می‌کرد کار انجام شده و نشده بود.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    created, updated, gcreated, gupdated = [], [], [], []
    dcreated, dupdated, notes, ambiguous, deletions = [], [], [], [], []
    for r in results:
        ambiguous += r.clarifications
        deletions += [d for d in r.deletion_requests if d not in deletions]
        created += r.created
        updated += r.updated
        gcreated += r.goals_created
        gupdated += r.goals_updated
        dcreated += r.debts_created
        dupdated += r.debts_updated
        notes += [n for n in r.notes if n not in notes]

    changed = bool(created or updated or gcreated or gupdated or dcreated or dupdated)
    asking = bool(ambiguous or deletions)
    # قبل از ساختِ کارت‌ها، تا کارتِ مشکوک از همان اول هشدار و دکمه‌ی «تکراریه» را داشته باشد.
    dup_line = duplicate_summary(duplicates_service.flag_new(user_id, created))

    memory.remember(user_id, "user", user_mem, weight=weight)

    model_reply = ""
    if weight == 1 and results:
        model_reply = (results[0].reply or "").strip()
        if model_reply in ("باشه", "باشه 🙂"):
            model_reply = ""

    if changed:
        reply = MULTIPART_REPLY if weight > 1 else (model_reply or RECORD_REPLY)
    elif model_reply and not claims_action(model_reply):
        reply = model_reply            # سؤالِ روشن‌کننده یا حرفِ انسانی
    elif asking or data_answer:
        reply = ""                     # سؤال/جوابِ دیتایی خودش پایین می‌آید
    else:
        reply = NOTHING_DONE

    extra = _update_lines(updated, dupdated, gupdated, skip=set(created))
    if extra:
        reply = "\n".join([reply, *extra]).strip() if reply else "\n".join(extra)
    if dup_line:
        reply = f"{reply}\n{dup_line}".strip()
    if data_answer:
        reply = f"{reply}\n\n{data_answer}".strip()
    for note in notes:
        reply = f"{reply}\n\n{note}".strip()
    notice = _threshold_notice(used, used + weight)
    if notice:
        reply = f"{reply}\n\n{notice}".strip()
    if reply:
        await update.message.reply_text(reply)
        memory.remember(user_id, "assistant", reply)

    shown: set[int] = set()
    for tid in created:
        await send_card(context.bot, chat_id, tid)
        shown.add(tid)
    for tid in updated:
        if tid not in shown:
            await refresh_card(context.bot, chat_id, tid)
    dshown: set[int] = set()
    for did in dcreated:
        await send_debt_card(context.bot, chat_id, did)
        dshown.add(did)
    for did in dupdated:
        if did not in dshown:
            await refresh_debt_card(context.bot, chat_id, did)

    # موردهای مبهم ثبت نشده‌اند؛ به‌جای حدس، همین‌جا از کاربر می‌پرسیم.
    for clar_id in ambiguous:
        question = clarify_service.question(clar_id)
        if question:
            await _ask(update, user_id, question, clarify_keyboard(clar_id))

    # حذف با حرف‌زدن هیچ‌وقت مستقیم انجام نمی‌شود — برگشت‌ناپذیر است؛ اول تأیید.
    for txn_id in deletions:
        txn = repo.get_transaction(txn_id)
        if txn:
            title = (txn.get("title") or "").strip() or "(بی‌عنوان)"
            amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
            await _ask(update, user_id, f"🗑 این رو حذف کنم؟\n{title} — {amount} (#{txn_id})",
                       delete_confirm_keyboard(txn_id))

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


async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                   user_text: str | None = None, audio_file_id: str | None = None,
                   voice_duration: int = 0, context_note: str = "",
                   force_record: bool = False) -> None:
    """force_record: ریپلای روی چیزی که به یک رکوردِ مشخص اشاره می‌کند (کارت، یادآوری)
    تقریباً همیشه درباره‌ی همان رکورد است؛ آن را از مسیرِ گفتگوییِ روتر عبور نمی‌دهیم تا
    با یک جوابِ خوش‌لحن ولی بی‌اثر گم نشود."""
    user_id = update.effective_user.id

    # راهنمای نیت یک‌بارمصرف است: همین پیام از آن استفاده می‌کند و بعد پاک می‌شود.
    hint = context.user_data.pop(INTENT_HINT_KEY, None)
    if hint and INTENT_HINTS.get(hint):
        context_note = f"{context_note} {INTENT_HINTS[hint]}".strip()

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
        hist = memory.history(user_id)
        # دفترِ فعلی — تا مدل بداند «اون ۸۶ تومنی» کدام تراکنش است.
        book = ledger.snapshot(user_id)
        short_voice = bool(audio_file_id) and voice_duration <= settings.voice_oneshot_max_seconds

        # مرحله‌ی ۱ — آماده‌سازیِ متن.
        # ویسِ کوتاه یک کالِ استخراج می‌خورد؛ اگر چیزی ثبت شد همان کافی است (بدون روتر).
        if short_voice:
            tg_file = await context.bot.get_file(audio_file_id)
            blob = bytes(await tg_file.download_as_bytearray())
            r = await agent.converse_audio(
                audio_ogg=blob, user_id=user_id, history=hist,
                profile=memory.profile(user_id),
                allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
                context_note=context_note, ledger=book,
            )
            text = (r.transcript or "").strip()
            if (r.created or r.updated or r.goals_created or r.goals_updated
                    or r.debts_created or r.debts_updated or r.clarifications
                    or r.deletion_requests or force_record):
                await status_msg.delete()
                await _emit(update, context, results=[r], weight=1,
                            user_mem=text or "(ویس)", used=used, data_answer="")
                return
            # ویس چیزی ثبت نکرد → احتمالاً سؤال یا گفتگو بود؛ با روتر ادامه بده.
        else:
            if audio_file_id:
                tg_file = await context.bot.get_file(audio_file_id)
                blob = bytes(await tg_file.download_as_bytearray())
                user_text = await agent.transcribe(blob)
            text = (user_text or "").strip()

        # مرحله‌ی ۲ — نیت‌خوانیِ ارزان.
        decision = await router.route(text=text, history=hist, reply_note=context_note,
                                      ledger=book)
        # روتر هیچ‌کاری انجام نمی‌دهد. اگر در جوابش قولِ انجامِ کاری داد («الان اصلاحش
        # می‌کنم»)، یعنی خودش فهمیده کاربر تغییری می‌خواهد ولی پرچمِ record را نزده —
        # همان باگِ گزارش‌شده. آن قول را نشان نمی‌دهیم؛ کار را واقعاً اجرا می‌کنیم و نتیجه‌ی
        # واقعی را می‌گوییم.
        if force_record or (not decision.record and claims_action(decision.reply)):
            decision.record = True
        data_answer = ""
        if decision.data_query:
            question = f"{context_note}\n\n{text}" if context_note else text
            data_answer = await agent.answer_data(question, hist, user_id)

        # مسیرِ گفتگو/گزارش (بدون ثبت): همین‌جا جواب بده، بدون کارت.
        if not decision.record:
            await status_msg.delete()
            reply = decision.reply
            if data_answer:
                reply = f"{reply}\n\n{data_answer}".strip() if reply else data_answer
            reply = reply or FALLBACK_REPLY
            notice = _threshold_notice(used, used + 1)
            if notice:
                reply = f"{reply}\n\n{notice}"
            memory.remember(user_id, "user", text or "(ویس)", weight=1)
            await update.message.reply_text(reply)
            memory.remember(user_id, "assistant", reply)
            return

        # مسیرِ ثبت/ویرایش/هدف (در صورت لزوم چانک می‌شود).
        if decide_parts(text) == -1:
            await status_msg.edit_text(TOO_LONG_MSG)
            return
        results, weight = await _text_pipeline(text, context_note, user_id, book)
    except LLMUnavailableError:
        await status_msg.edit_text(USER_FACING_UNAVAILABLE)
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در پردازش پیام")
        await status_msg.edit_text("یه مشکلی پیش اومد. دوباره تلاش کن.")
        return

    await status_msg.delete()
    # جوابِ روتر عمداً اینجا استفاده نمی‌شود: روتر قبل از اجرا حرف زده و نمی‌داند چه شد.
    await _emit(update, context, results=results, weight=weight,
                user_mem=text or "(ویس)", used=used, data_answer=data_answer)


# ---------- ویرایش دکمه‌ای ----------

async def _finish_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       awaiting: dict, confirmation: str) -> None:
    """پایانِ موفقِ ویرایش: پیام‌های موقت پاک، state پاک، و یک تأییدِ صریح به کاربر.

    قبلاً هیچ بازخوردی نبود و کاربر نمی‌فهمید ویرایش گرفت یا نه؛ کارت بی‌صدا عوض می‌شد.
    """
    await clear_edit_messages(context.bot, awaiting)
    context.user_data.pop(AWAITING_KEY, None)
    await update.message.reply_text(confirmation)


async def _reject_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       awaiting: dict, message: str) -> None:
    """ورودی نامعتبر: state نگه داشته می‌شود تا دوباره تلاش کند، ولی خطا هم باید بعداً پاک شود."""
    sent = await update.message.reply_text(message, reply_markup=cancel_keyboard())
    awaiting.setdefault("cleanup", []).append(sent.message_id)


async def _apply_button_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             awaiting: dict, text: str) -> None:
    kind = awaiting.get("kind", "txn")
    obj_id = awaiting["id"]
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if kind == "debt":
        debt = repo.get_debt(obj_id)
        if debt is None:
            await _finish_edit(update, context, awaiting, "این مورد دیگر وجود ندارد.")
            return
        if awaiting["action"] == "amount":
            value = parse_amount(text)
            if value is None:
                await _reject_edit(update, context, awaiting,
                                   "عدد معتبر نبود. فقط رقم بفرست (مثلاً ۵۰۰۰۰۰).")
                return
            debts_service.apply_update(user_id, obj_id, {"amount": value})
            currency = (repo.get_debt(obj_id) or {}).get("currency_display", "toman")
            confirmation = f"✅ مبلغ شد {format_amount(value, currency)}"
        else:  # counterparty
            party = text.strip()
            debts_service.apply_update(user_id, obj_id, {"counterparty": party})
            confirmation = f"✅ طرفِ حساب شد «{party}»"
        await _finish_edit(update, context, awaiting, confirmation)
        await refresh_debt_card(context.bot, chat_id, obj_id)
        return

    if kind == "goal":
        if repo.get_goal(obj_id) is None:
            await _finish_edit(update, context, awaiting, "این هدف دیگر وجود ندارد.")
            return
        if awaiting["action"] == "limit":
            value = parse_amount(text)
            if value is None:
                await _reject_edit(update, context, awaiting,
                                   "عدد معتبر نبود. فقط رقم بفرست.")
                return
            repo.update_goal(obj_id, limit_amount=value)
            confirmation = f"✅ سقف این هدف شد {format_amount(value, 'toman')}"
        else:  # topic
            topic = text.strip()
            goals_service.apply_update(user_id, obj_id, {"topic": topic})
            confirmation = f"✅ موضوع این هدف شد «{topic}»"
        goals_service._sync_status(obj_id)
        await _finish_edit(update, context, awaiting, confirmation)
        await refresh_goal_card(context.bot, chat_id, obj_id)
        await goals_service.evaluate_and_alert(context.bot, user_id)
        return

    txn = repo.get_transaction(obj_id)
    if txn is None:
        await _finish_edit(update, context, awaiting, "این تراکنش دیگر وجود ندارد.")
        return
    if awaiting["action"] == "amount":
        value = parse_amount(text)
        if value is None:
            await _reject_edit(update, context, awaiting,
                               "عدد معتبر نبود. فقط رقم بفرست (مثلاً ۲۵۰۰۰۰).")
            return
        repo.update_transaction(obj_id, amount=value)
        confirmation = f"✅ مبلغ شد {format_amount(value, txn.get('currency_display', 'toman'))}"
    else:  # title
        title = text.strip()
        repo.update_transaction(obj_id, title=title)
        confirmation = f"✅ عنوان شد «{title}»"
    repo.sync_status(obj_id)
    await _finish_edit(update, context, awaiting, confirmation)
    await refresh_card(context.bot, chat_id, obj_id)
    await goals_service.evaluate_and_alert(context.bot, user_id)
