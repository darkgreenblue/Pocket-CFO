"""حافظه‌ی مکالمه: کوتاه‌مدت (پنجره‌ی اخیر) و بلندمدت (پروفایل کاربر).

پروفایل بلندمدت هر شب با یک کال Gemini Flash از روی مکالمات همان روز آپدیت می‌شود
تا شناخت دستیار از کاربر (اهداف، دغدغه‌ها، عادت‌های مالی) به‌مرور کامل‌تر شود.
"""
from __future__ import annotations

import logging

from bot.config import settings
from bot.db import repo
from bot.llm.client import complete_text
from bot.llm.prompts import PROFILE_UPDATE_PROMPT

logger = logging.getLogger(__name__)


def history(user_id: int) -> list[dict]:
    return repo.recent_messages(user_id, settings.chat_history_turns)


def remember(user_id: int, role: str, content: str) -> None:
    if content and content.strip():
        repo.add_message(user_id, role, content.strip())


def profile(user_id: int) -> str:
    return repo.get_profile(user_id)


async def update_profile_from_day(user_id: int, since_iso: str) -> bool:
    """پروفایل کاربر را از روی مکالمات روز به‌روز می‌کند. True اگر آپدیت شد."""
    msgs = repo.messages_since(user_id, since_iso)
    if not msgs:
        return False
    today = "\n".join(
        f"{'کاربر' if m['role'] == 'user' else 'دستیار'}: {m['content']}" for m in msgs
    )
    prompt = PROFILE_UPDATE_PROMPT.format(
        old_profile=repo.get_profile(user_id) or "(خالی)",
        today=today,
    )
    try:
        new_profile = await complete_text(
            settings.llm_primary_model,
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("آپدیت پروفایل کاربر %s ناموفق: %s", user_id, exc)
        return False
    if new_profile.strip():
        repo.set_profile(user_id, new_profile.strip())
        return True
    return False
