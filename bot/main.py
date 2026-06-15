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
from bot.handlers.commands import cmd_report, cmd_start
from bot.handlers.messages import handle_text, handle_voice
from bot.services.reminders import nightly_reminder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    repo.init_db()
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CallbackQueryHandler(on_callback))
    # ویس اولویت دارد تا قانون «لغو ویرایش هنگام ویس» درست اعمال شود.
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue is not None:
        app.job_queue.run_daily(
            nightly_reminder,
            time=dt.time(hour=settings.reminder_hour, minute=0, tzinfo=_TEHRAN),
            name="nightly_reminder",
        )
    return app


def main() -> None:
    app = build_application()
    logger.info("CFO جیبی راه افتاد. در انتظار پیام‌ها…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
