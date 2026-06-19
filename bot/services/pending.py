"""صفِ پیام‌های بعد-از-ساعت‌کاری: ذخیره و سپس ثبتِ یکپارچه‌ی صبح.

وقتی سهمیه‌ی روزانه تمام می‌شود، پیام‌های کاربر (به‌صورت متن) در صف می‌مانند و یک‌جا —
یا صبح سرِ ساعت، یا در اولین تعاملِ مجاز روز بعد — در یک درخواست LLM ثبت می‌شوند.
"""
from __future__ import annotations

import logging

from bot.db import repo
from bot.handlers.cards import send_card
from bot.llm import agent
from bot.services import memory
from bot.services import tags as tags_service

logger = logging.getLogger(__name__)

WAIT_MSG = "📝 دارم تراکنش‌های باقی‌مانده‌ی قبلی‌ات رو یکجا ثبت می‌کنم…"


async def flush_pending(bot, user_id: int) -> bool:
    """صفِ کاربر را در یک درخواست ثبت می‌کند. True اگر چیزی پردازش شد."""
    items = repo.get_pending(user_id)
    if not items:
        return False

    text_parts = [i["content"] for i in items if i["kind"] == "text"]
    voice_ids = [i["content"] for i in items if i["kind"] == "voice"]

    wait = await bot.send_message(chat_id=user_id, text=WAIT_MSG)

    audio_blobs: list[bytes] = []
    for fid in voice_ids:
        try:
            f = await bot.get_file(fid)
            audio_blobs.append(bytes(await f.download_as_bytearray()))
        except Exception:  # noqa: BLE001
            logger.warning("دانلود ویسِ صف (%s) ناموفق بود", fid)

    try:
        result = await agent.converse_batch(
            text_parts=text_parts, audio_blobs=audio_blobs, user_id=user_id,
            history=memory.history(user_id), profile=memory.profile(user_id),
            allowed_tags=tags_service.allowed_tag_names(repo.get_tags()),
        )
    except Exception:  # noqa: BLE001
        logger.exception("پردازش صف برای %s ناموفق بود", user_id)
        try:
            await wait.edit_text("ثبت تراکنش‌های قبلی الان ممکن نشد؛ بعداً دوباره تلاش می‌کنم.")
        except Exception:  # noqa: BLE001
            pass
        return False

    repo.clear_pending(user_id)
    memory.remember(user_id, "user", f"[صفِ بعد از ساعت کاری: {len(items)} پیام]")  # یک کوپن
    memory.remember(user_id, "assistant", result.reply or "ثبت شد.")
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass
    await bot.send_message(
        chat_id=user_id,
        text="✅ هر چیزی که بعد از پایان سهمیه گفته بودی ثبت کردم. دیگه چیزی از قبل نمونده 🙂",
    )
    for tid in result.created:
        await send_card(bot, user_id, tid)
    return True


async def morning_job(context) -> None:
    """سرِ ساعتِ صبح، صفِ همه‌ی کاربران را پردازش می‌کند."""
    for user_id in repo.users_with_pending():
        try:
            await flush_pending(context.bot, user_id)
        except Exception:  # noqa: BLE001
            logger.exception("morning_job برای %s ناموفق بود", user_id)
