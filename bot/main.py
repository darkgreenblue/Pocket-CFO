"""نقطه‌ی ورود ربات «CFO جیبی»."""
from __future__ import annotations

import datetime as dt
import logging

try:
    from zoneinfo import ZoneInfo
    _TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:  # noqa: BLE001 — اگر دیتابیس tz نبود، از UTC استفاده کن
    _TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.db import repo
from bot.handlers.callbacks import on_callback
from bot.handlers.commands import cmd_household, cmd_report, cmd_shortcut, cmd_start
from bot.handlers.messages import handle_text, handle_unsupported, handle_voice
from bot.ingest.server import IngestServer
from bot.services.ingest import deliver_undelivered_cards, purge_old_requests
from bot.services.pending import morning_job
from bot.services.reminders import nightly_profile_update, nightly_reminder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    repo.init_db()
    ingest_server = IngestServer()

    async def _open_ingest(app: Application) -> None:
        await ingest_server.start(app.bot)

    async def _close_ingest(_app: Application) -> None:
        await ingest_server.stop()

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_open_ingest)
        .post_shutdown(_close_ingest)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("household", cmd_household))
    app.add_handler(CommandHandler("shortcut", cmd_shortcut))
    app.add_handler(CallbackQueryHandler(on_callback))
    # فقط ویس تلگرام پذیرفته می‌شود؛ فایل صوتی/تصویری رد می‌شود.
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE | filters.PHOTO | filters.Document.ALL,
            handle_unsupported,
        )
    )

    if app.job_queue is not None:
        # یادآوری تراکنش‌های ناقص
        app.job_queue.run_daily(
            nightly_reminder,
            time=dt.time(hour=settings.reminder_hour, minute=0, tzinfo=_TEHRAN),
            name="nightly_reminder",
        )
        # آپدیت پروفایل بلندمدت کاربر از مکالمات روز (کمی دیرتر)
        app.job_queue.run_daily(
            nightly_profile_update,
            time=dt.time(hour=settings.reminder_hour, minute=30, tzinfo=_TEHRAN),
            name="nightly_profile_update",
        )
        # پردازشِ صبحِ صفِ پیام‌های بعد-از-ساعت‌کاری
        app.job_queue.run_daily(
            morning_job,
            time=dt.time(hour=settings.morning_hour, minute=0, tzinfo=_TEHRAN),
            name="morning_flush",
        )
        if settings.ingest_enabled:
            # کارتِ تراکنشی که از میان‌بر ثبت شد ولی لحظه‌ی ثبت به تلگرام نرسید.
            app.job_queue.run_repeating(
                deliver_undelivered_cards, interval=300, first=60,
                name="ingest_card_retry",
            )
            app.job_queue.run_daily(
                purge_old_requests,
                time=dt.time(hour=4, minute=0, tzinfo=_TEHRAN),
                name="ingest_purge",
            )
    return app


def main() -> None:
    app = build_application()
    # وضعیتِ واقعیِ دسترسی را لاگ می‌کنیم: لیستِ .env روی سرور از بیرون دیده نمی‌شود و
    # تنها راهِ فهمیدنِ اینکه ربات الان محدود است یا باز، همین خط در `docker logs` است.
    logger.info("%s", settings.access_summary())
    logger.info("CFO جیبی راه افتاد. در انتظار پیام‌ها…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
