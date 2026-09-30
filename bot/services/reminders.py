"""یادآوری شبانه‌ی هوشمند.

فقط تراکنش‌هایی پیگیری می‌شوند که:
  ۱) کاربر گفته بعداً تکمیل می‌کند (needs_later_completion=1)، یا
  ۲) مبلغ یا عنوانشان هنوز خالی مانده (draft ناقص).
بعد از یادآوری، فلگ reminded ست می‌شود تا تکراری نشود.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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


def _known_users() -> list[int]:
    """همه‌ی کاربرانِ شناخته‌شده (اعضای خانوارها) + آی‌دی‌های مجازِ تنظیمات."""
    users = dict.fromkeys(repo.all_member_user_ids())
    for uid in settings.allowed_user_ids or ():
        users.setdefault(uid, None)
    return list(users)


MAX_BUTTON_ROWS = 10   # بیشتر از این، پیام شلوغ می‌شود؛ بقیه با ریپلای کامل می‌شوند


def reminder_keyboard(pending: list[dict]) -> Optional[InlineKeyboardMarkup]:
    """برای هر تراکنشِ ناقص، دکمه‌ی همان فیلدی که کم دارد — تا تکمیل یک ضربه باشد، نه حدس.

    از همان callbackهای کارت استفاده می‌کند، پس ویرایش دقیقاً مثل کارت رفتار می‌کند
    (پرسش با انصراف، تأییدِ صریح، پاک‌شدنِ پیام‌های موقت).
    """
    rows = []
    for txn in pending[:MAX_BUTTON_ROWS]:
        row = []
        if not (txn.get("title") or "").strip():
            row.append(InlineKeyboardButton(f"✏️ عنوانِ #{txn['id']}",
                                            callback_data=f"edittitle:{txn['id']}"))
        if txn.get("amount") is None:
            row.append(InlineKeyboardButton(f"✏️ مبلغِ #{txn['id']}",
                                            callback_data=f"editamt:{txn['id']}"))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None


def reminder_text(pending: list[dict]) -> str:
    lines = ["🌙 سلام! چند تراکنش هست که می‌تونیم امشب کاملش کنیم:", ""]
    lines += [_describe(t) for t in pending]
    lines.append("")
    lines.append("با دکمه‌ها کاملشون کن، یا همین‌جا بگو کدوم چی بوده "
                 "(مثلاً «اون ۸۶ تومنی آب معدنی بود»). 🙂")
    return "\n".join(lines)


async def nightly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in _known_users():
        pending = repo.pending_for_reminder(user_id)
        if not pending:
            continue
        text = reminder_text(pending)
        try:
            await context.bot.send_message(chat_id=user_id, text=text,
                                           reply_markup=reminder_keyboard(pending))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ارسال یادآوری به %s ناموفق بود: %s", user_id, exc)
            continue
        # یادآوری باید در حافظه باشد: کاربر معمولاً بلافاصله جوابش را می‌دهد، و بدونِ این
        # مدل نمی‌داند «این ۸۶ هزار» یعنی چه — دقیقاً باگی که گزارش شد.
        memory.remember_bot(user_id, text)
        for txn in pending:
            repo.mark_reminded(txn["id"])


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
