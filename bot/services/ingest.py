"""درِ دومِ ورودی: ثبتِ خرج بدون عبور از تلگرام.

**چرا وجود دارد.** گوشیِ کاربر همیشه به تلگرام دسترسی ندارد، ولی به سرورِ خودمان
معمولاً دارد. پس جهتِ ترافیک را برعکس می‌کنیم: گوشی → سرورِ ما (ثبت در دیتابیس)، و
بعد سرورِ ما → تلگرام (فرستادنِ کارتِ تأیید). سرور خودش به تلگرام دسترسی دارد، پس
کاربر می‌تواند بدونِ دسترسی به تلگرام خرجش را ثبت کند و هر وقت بعداً تلگرام را باز
کرد، کارت آنجا منتظرش است تا ویرایش/تأیید کند.

**فقط ثبتِ تراکنش.** این مسیر عمداً باریک است: نه گزارش می‌دهد، نه بدهی/هدف ثبت
می‌کند، نه چیزی را ویرایش می‌کند. اگر مدل چیزی جز خرج دید، هیچ اثری روی دیتابیس
نمی‌گذارد و در عوض متنِ رونویسی‌شده را در تلگرام برای کاربر می‌فرستد تا گم نشود.
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from bot.config import settings
from bot.db import repo
from bot.handlers.cards import send_card
from bot.llm import agent
from bot.llm.client import LLMUnavailableError
from bot.services import goals as goals_service
from bot.services import household as household_service
from bot.services import memory
from bot.services import tags as tags_service
from bot.utils import ratelimit

logger = logging.getLogger(__name__)

SOURCE = "shortcut"
MAX_TEXT_CHARS = 1000
# فرمت‌هایی که به مدل پاس می‌دهیم. شرتکاتِ iOS معمولاً m4a می‌دهد.
ALLOWED_AUDIO_FORMATS = {"ogg", "m4a", "mp3", "wav", "mp4", "aac"}

# پیام‌هایی که شرتکات به‌صورت نوتیفیکیشن نشان می‌دهد (کوتاه، چون روی قفلِ صفحه می‌آید).
MSG_RECORDED = "ثبت شد ✅ کارتش در تلگرام منتظرته."
MSG_QUEUED = "ذخیره شد 📥 سهمیه‌ی امروز تموم شده؛ صبح ثبتش می‌کنم."
MSG_NOTHING = "خرجی توش پیدا نکردم — متنش رو در تلگرام برات فرستادم."
MSG_RATE_LIMITED = "یه کم آروم‌تر 🙂 چند لحظه دیگه دوباره بفرست."
MSG_LLM_DOWN = "سرویس هوش مصنوعی جواب نداد. دوباره بفرست."

# متنی که وقتی ورودی خرج نبود در تلگرام می‌رود — هیچ ورودی‌ای نباید بی‌صدا گم شود.
TELEGRAM_NOT_EXPENSE = (
    "📥 این رو از میان‌برِ گوشیت فرستادی ولی خرجی توش پیدا نکردم، پس چیزی ثبت نشد:\n\n"
    "«{text}»\n\n"
    "اگر باید ثبت بشه، همین‌جا برام بنویس یا ویس بگیر."
)
TELEGRAM_DROPPED = (
    "ℹ️ از میان‌برِ گوشی فقط «خرج» ثبت می‌شه. این‌ها رو دیدم ولی ثبت نکردم: {kinds}.\n"
    "برای این‌ها همین‌جا در تلگرام بگو."
)


class IngestAuthError(Exception):
    """توکن نامعتبر بود یا کاربر اجازه‌ی ورود ندارد."""


@dataclass
class IngestResult:
    status: str      # recorded | queued | nothing | duplicate | rate_limited | error
    message: str

    @property
    def ok(self) -> bool:
        return self.status not in ("error", "rate_limited")


def resolve_user(token: str) -> int:
    """توکن را به user_id تلگرام تبدیل می‌کند (یا خطا)."""
    token = (token or "").strip()
    # مقایسه‌ی constant-time روی همه‌ی توکن‌ها تا طولِ پاسخ چیزی لو ندهد.
    match: Optional[int] = None
    for known, user_id in settings.ingest_tokens.items():
        if hmac.compare_digest(known, token):
            match = user_id
    if match is None:
        raise IngestAuthError("توکن نامعتبر")
    if not household_service.authorized(match):
        raise IngestAuthError("کاربر مجاز نیست")
    return match


def normalize_audio_format(fmt: str, fallback: str = "m4a") -> str:
    fmt = (fmt or "").strip().lstrip(".").lower()
    return fmt if fmt in ALLOWED_AUDIO_FORMATS else fallback


def _save_audio(user_id: int, blob: bytes, fmt: str) -> str:
    """ویس را روی دیسک نگه می‌دارد تا صبح از صف پردازش شود."""
    directory = settings.ingest_audio_dir
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{user_id}-{uuid.uuid4().hex}.{fmt}")
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


async def handle(
    bot,
    *,
    user_id: int,
    request_id: str,
    text: str = "",
    audio: Optional[tuple[bytes, str]] = None,
) -> IngestResult:
    """یک ورودیِ شرتکات را کامل پردازش می‌کند و کارت را به تلگرام می‌فرستد."""
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text and audio is None:
        return IngestResult("error", "چیزی نفرستادی.")

    # ۱) تکراری؟ رزروِ اتمیک تا دو POSTِ هم‌زمان دو تراکنش نسازند.
    if not repo.claim_ingest_request(request_id, user_id):
        prev = repo.find_ingest_request(request_id) or {}
        return IngestResult("duplicate", prev.get("message") or "قبلاً ثبتش کردم ✅")

    try:
        return await _process(bot, user_id, request_id, text, audio)
    except Exception:  # noqa: BLE001
        logger.exception("پردازشِ ورودیِ شرتکات ناموفق بود (user=%s)", user_id)
        # رزرو را پس می‌دهیم تا کاربر بتواند همان درخواست را دوباره بفرستد.
        repo.release_ingest_request(request_id)
        return IngestResult("error", "یه مشکلی پیش اومد. دوباره بفرست.")


async def _process(bot, user_id: int, request_id: str, text: str,
                   audio: Optional[tuple[bytes, str]]) -> IngestResult:
    if not ratelimit.allow(user_id, settings.rate_limit_max, settings.rate_limit_window):
        repo.release_ingest_request(request_id)
        return IngestResult("rate_limited", MSG_RATE_LIMITED)

    # ۲) سقفِ روزانه: مثل خودِ تلگرام رعایت می‌شود، وگرنه این مسیر یک راهِ دورزدنِ سقف است.
    if memory.usage_today(user_id) >= settings.daily_llm_limit:
        if audio is not None:
            blob, fmt = audio
            repo.add_pending(user_id, "voice_file", _save_audio(user_id, blob, fmt))
        else:
            repo.add_pending(user_id, "text", text)
        repo.finish_ingest_request(request_id, "queued", MSG_QUEUED)
        return IngestResult("queued", MSG_QUEUED)

    # ۳) استخراج — فقط تراکنش.
    try:
        result = await agent.converse_expense_only(
            user_text=text, audio=audio, user_id=user_id,
            history=memory.history(user_id), profile=memory.profile(user_id),
            allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
        )
    except LLMUnavailableError:
        repo.release_ingest_request(request_id)
        return IngestResult("error", MSG_LLM_DOWN)

    spoken = result.transcript or text
    memory.remember(user_id, "user", spoken or "(ویسِ میان‌بر)")

    # ۴) چیزی ثبت نشد؟ ورودی نباید گم شود — متنش را در تلگرام بفرست.
    if not result.created:
        await _notify(bot, user_id, TELEGRAM_NOT_EXPENSE.format(text=spoken or "(ویس)"))
        repo.finish_ingest_request(request_id, "nothing", MSG_NOTHING)
        return IngestResult("nothing", MSG_NOTHING)

    # ۵) کارت‌ها به تلگرام. اگر نرفت، تراکنش سرِ جایش می‌ماند و job دوره‌ای دوباره
    #    تلاش می‌کند (card_message_id هنوز NULL است).
    for txn_id in result.created:
        try:
            await send_card(bot, user_id, txn_id)
        except Exception:  # noqa: BLE001
            logger.warning("ارسالِ کارتِ #%s به تلگرام ناموفق بود؛ بعداً دوباره", txn_id)

    if result.dropped_kinds:
        await _notify(bot, user_id, TELEGRAM_DROPPED.format(kinds="، ".join(result.dropped_kinds)))

    try:
        await goals_service.evaluate_and_alert(bot, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("ارزیابیِ هدف بعد از ورودیِ شرتکات ناموفق بود")

    message = MSG_RECORDED if len(result.created) == 1 else f"{len(result.created)} تراکنش ثبت شد ✅"
    repo.finish_ingest_request(request_id, "recorded", message)
    return IngestResult("recorded", message)


async def _notify(bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=user_id, text=text)
    except Exception:  # noqa: BLE001
        logger.warning("ارسالِ پیامِ اطلاع‌رسانی به %s ناموفق بود", user_id)


# ---------- تحویلِ کارت‌های جامانده ----------

RETRY_WINDOW_DAYS = 7


async def deliver_undelivered_cards(context) -> None:
    """کارت‌هایی که موقعِ ثبت به تلگرام نرسیدند را دوباره می‌فرستد.

    این همان چیزی است که ثبتِ آفلاین را واقعاً قابل‌اتکا می‌کند: تراکنش در دیتابیسِ
    ماست، پس حتی اگر تلگرام لحظه‌ی ثبت در دسترس نبود، کارت گم نمی‌شود.
    """
    since = (dt.datetime.now() - dt.timedelta(days=RETRY_WINDOW_DAYS)).isoformat()
    for row in repo.undelivered_cards(SOURCE, since):
        try:
            await send_card(context.bot, row["user_id"], row["id"])
        except Exception:  # noqa: BLE001
            logger.warning("تلاشِ دوباره برای کارتِ #%s ناموفق بود", row["id"])


async def purge_old_requests(context) -> None:
    """کلیدهای idempotency قدیمی را پاک می‌کند (جدول بی‌نهایت رشد نکند)."""
    before = (dt.datetime.now() - dt.timedelta(days=RETRY_WINDOW_DAYS)).isoformat()
    repo.purge_ingest_requests(before)
