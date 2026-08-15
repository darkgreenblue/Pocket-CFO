"""خانوار: مدیریت مالیِ مشترکِ چند اکانت روی یک دفترِ واحد.

قاعده‌ها:
— هر کاربر دقیقاً عضو یک خانوار است؛ کاربرِ تنها هم یک خانوارِ تک‌نفره دارد تا کلِ
  سیستم یک‌شکل بماند و با join شدنِ پارتنر چیزی نشکند.
— هر تراکنش/هدف/بدهی روی خانوار ثبت می‌شود؛ فقط «ثبت‌کننده» (user_id) جدا نگه داشته
  می‌شود تا در گزارش مشخص باشد چه کسی چه چیزی را وارد کرده.
— دسترسی: کسی که با لینکِ دعوت وارد شده، به‌واسطه‌ی مالکِ خانوار مجاز است (لازم نیست
  آی‌دی‌اش دستی در ALLOWED_USER_IDS باشد).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from bot.config import settings
from bot.db import repo

logger = logging.getLogger(__name__)

# نسبت‌های قابل انتخاب هنگام دعوت (کد ← برچسب فارسی)
RELATIONS = {
    "partner": "پارتنر / همسر",
    "family": "عضو خانواده",
    "roommate": "هم‌خانه",
    "other": "سایر",
}

DEFAULT_NAME = "عضو خانوار"


def hid(user_id: int) -> int:
    """آی‌دی خانوارِ کاربر (در صورت نبود ساخته می‌شود)."""
    return repo.household_id_for(user_id)


def touch(user_id: int, display_name: str = "") -> int:
    """قبل از هر پردازش صدا زده می‌شود: خانوار را می‌سازد و نام نمایشی را تازه می‌کند."""
    return repo.touch_member(user_id, (display_name or "").strip()[:60])


def authorized(user_id: int) -> bool:
    """آیا این کاربر اجازه‌ی استفاده از ربات را دارد؟

    یا خودش در ALLOWED_USER_IDS است، یا عضو خانواری است که مالکش مجاز است.
    """
    if settings.is_authorized(user_id):
        return True
    household_id = repo.find_household_id(user_id)
    if household_id is None:
        return False
    household = repo.get_household(household_id)
    return bool(household) and settings.is_authorized(household["owner_id"])


def members(user_id: int) -> list[dict[str, Any]]:
    return repo.household_members(hid(user_id))


def member_ids(user_id: int) -> list[int]:
    return [m["user_id"] for m in members(user_id)]


def is_shared(user_id: int) -> bool:
    """خانوار بیش از یک عضو دارد؟ (گزارشِ تفکیکی فقط آن‌وقت معنا دارد.)"""
    return len(members(user_id)) > 1


def can_set_goals(user_id: int) -> bool:
    member = repo.get_member(user_id)
    if member is None:
        return True  # هنوز عضوی ثبت نشده؛ کاربرِ تنها محدودیتی ندارد
    return bool(member["can_set_goals"])


def display_name(user_id: int) -> str:
    member = repo.get_member(user_id)
    name = (member or {}).get("display_name") or ""
    return name.strip() or DEFAULT_NAME


def name_map(user_id: int) -> dict[int, str]:
    """نگاشتِ آی‌دی → نامِ نمایشیِ اعضای خانوار (برای گزارشِ «چه کسی ثبت کرده»)."""
    out: dict[int, str] = {}
    for m in members(user_id):
        out[m["user_id"]] = (m["display_name"] or "").strip() or DEFAULT_NAME
    return out


def is_owner(user_id: int) -> bool:
    member = repo.get_member(user_id)
    return bool(member) and member["role"] == "owner"


# ---------- دعوت ----------

def create_invite(user_id: int, relation: str, can_set_goals_flag: bool) -> str:
    """توکنِ یک‌بارمصرفِ دعوت می‌سازد (نسبت و سطح دسترسی روی خودِ لینک می‌نشیند)."""
    return repo.create_invite(
        household_id=hid(user_id), created_by=user_id,
        relation=RELATIONS.get(relation, relation), can_set_goals=can_set_goals_flag,
    )


def invite_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start={INVITE_PREFIX}{token}"


INVITE_PREFIX = "hh_"


def parse_invite_payload(payload: str) -> Optional[str]:
    """payloadِ /start را به توکنِ دعوت تبدیل می‌کند (اگر دعوت باشد)."""
    payload = (payload or "").strip()
    if payload.startswith(INVITE_PREFIX) and len(payload) > len(INVITE_PREFIX):
        return payload[len(INVITE_PREFIX):]
    return None


class JoinError(Exception):
    """دلیلِ کاربرپسندِ ناموفق‌بودنِ پیوستن."""


def accept_invite(token: str, user_id: int, display_name_value: str = "") -> dict[str, Any]:
    """دعوت را مصرف و کاربر را عضو خانوار می‌کند. خطاها به فارسی برمی‌گردند."""
    invite = repo.get_invite(token)
    if invite is None:
        raise JoinError("این لینکِ دعوت معتبر نیست. از عضوِ خانوار بخواه یک لینک تازه بسازد.")
    if invite["used_at"]:
        if invite["used_by"] == user_id:
            return invite
        raise JoinError("این لینکِ دعوت قبلاً استفاده شده. یک لینکِ تازه لازم است.")
    if invite["created_by"] == user_id:
        raise JoinError("این لینک را خودت ساخته‌ای 🙂 آن را برای طرفِ مقابل بفرست.")
    existing = repo.find_household_id(user_id)
    if existing is not None and existing == invite["household_id"]:
        raise JoinError("تو همین حالا هم عضو این خانوار هستی.")

    if not repo.mark_invite_used(token, user_id):
        raise JoinError("این لینکِ دعوت قبلاً استفاده شده. یک لینکِ تازه لازم است.")
    repo.join_household(
        household_id=invite["household_id"], user_id=user_id,
        relation=invite["relation"], can_set_goals=bool(invite["can_set_goals"]),
        display_name=(display_name_value or "").strip()[:60],
    )
    return invite
