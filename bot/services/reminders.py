"""یادآوری شبانه‌ی هوشمند.

فقط تراکنش‌هایی پیگیری می‌شوند که:
  ۱) کاربر گفته بعداً تکمیل می‌کند (needs_later_completion=1)، یا
  ۲) مبلغ یا عنوانشان هنوز خالی مانده (draft ناقص).
بعد از یادآوری، فلگ reminded ست می‌شود تا تکراری نشود.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.services import memory
from bot.utils.money import format_amount

logger = logging.getLogger(__name__)


def _start_of_today_iso() -> str:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _describe(txn: dict) -> str:
    title = txn.get("title") or "بدون عنوان"
    amount = format_amount(txn.get("amount"), txn.get("currency_display", "toman"))
    missing = []
    if not txn.get("title"):
        missing.append("عنوان")
    if txn.get("amount") is None:
        missing.append("مبلغ")
    tail = f" (نیازمند: {' و '.join(missing)})" if missing else ""
    return f"• #{txn['id']} — {title} | {amount}{tail}"


async def nightly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in settings.allowed_user_ids or set():
        pending = repo.pending_for_reminder(user_id)
        if not pending:
            continue
        lines = ["🌙 سلام! چند تراکنش هست که می‌تونیم امشب کاملش کنیم:", ""]
        lines += [_describe(t) for t in pending]
        lines.append("")
        lines.append("اگه دوست داری، روی هر کدوم جواب بده تا توضیحات بیشترش رو با متن یا ویس کامل کنیم. 🙂")
        try:
            await context.bot.send_message(chat_id=user_id, text="\n".join(lines))
            for txn in pending:
                repo.mark_reminded(txn["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("ارسال یادآوری به %s ناموفق بود: %s", user_id, exc)


async def nightly_profile_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """آخر هر روز، پروفایل بلندمدت هر کاربرِ فعال را از مکالمات روز به‌روز می‌کند."""
    since = _start_of_today_iso()
    for user_id in repo.users_with_messages_since(since):
        try:
            updated = await memory.update_profile_from_day(user_id, since)
            if updated:
                logger.info("پروفایل کاربر %s به‌روز شد", user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("آپدیت پروفایل کاربر %s ناموفق بود: %s", user_id, exc)
