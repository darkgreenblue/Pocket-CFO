"""صفِ پیام‌های بعد-از-ساعت‌کاری: ذخیره و سپس ثبتِ یکپارچه‌ی صبح.

وقتی سهمیه‌ی روزانه تمام می‌شود، پیام‌های کاربر (به‌صورت متن) در صف می‌مانند و یک‌جا —
یا صبح سرِ ساعت، یا در اولین تعاملِ مجاز روز بعد — در یک درخواست LLM ثبت می‌شوند.
"""
from __future__ import annotations

import logging
import os

from bot.db import repo
from bot.handlers.cards import send_card, send_debt_card, send_goal_card
from bot.llm import agent
from bot.services import goals
from bot.services import memory
from bot.services import tags as tags_service

logger = logging.getLogger(__name__)

WAIT_MSG = "📝 دارم تراکنش‌های باقی‌مانده‌ی قبلی‌ات رو یکجا ثبت می‌کنم…"


def _format_of(path: str) -> str:
    """فرمتِ صوت را از پسوندِ فایلِ ذخیره‌شده درمی‌آورد."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext or "ogg"


async def flush_pending(bot, user_id: int) -> bool:
    """صفِ کاربر را در یک درخواست ثبت می‌کند. True اگر چیزی پردازش شد."""
    items = repo.get_pending(user_id)
    if not items:
        return False

    text_parts = [i["content"] for i in items if i["kind"] == "text"]
    voice_ids = [i["content"] for i in items if i["kind"] == "voice"]
    # ویسِ شرتکات file_id تلگرامی ندارد؛ روی دیسکِ خودمان ذخیره شده است.
    voice_paths = [i["content"] for i in items if i["kind"] == "voice_file"]

    wait = await bot.send_message(chat_id=user_id, text=WAIT_MSG)

    audio_items: list[tuple[bytes, str]] = []
    for fid in voice_ids:
        try:
            f = await bot.get_file(fid)
            audio_items.append((bytes(await f.download_as_bytearray()), "ogg"))
        except Exception:  # noqa: BLE001
            logger.warning("دانلود ویسِ صف (%s) ناموفق بود", fid)
    for path in voice_paths:
        try:
            with open(path, "rb") as fh:
                audio_items.append((fh.read(), _format_of(path)))
        except OSError:
            logger.warning("خواندن ویسِ صف‌شده (%s) ناموفق بود", path)

    try:
        result = await agent.converse_batch(
            text_parts=text_parts, audio_items=audio_items, user_id=user_id,
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
    for path in voice_paths:
        try:
            os.remove(path)
        except OSError:
            pass
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
    for did in result.debts_created:
        await send_debt_card(bot, user_id, did)
    for gid in result.goals_created:
        await send_goal_card(bot, user_id, gid)
    if result.created or result.updated or result.goals_created or result.goals_updated:
        await goals.evaluate_and_alert(bot, user_id)
    return True


async def morning_job(context) -> None:
    """سرِ ساعتِ صبح، صفِ همه‌ی کاربران را پردازش می‌کند."""
    for user_id in repo.users_with_pending():
        try:
            await flush_pending(context.bot, user_id)
        except Exception:  # noqa: BLE001
            logger.exception("morning_job برای %s ناموفق بود", user_id)
