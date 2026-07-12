"""اهداف مالی: ساخت/به‌روزرسانی هدف و موتور آلارمِ ۵۰/۸۰/۱۰۰٪.

محاسبه‌ی مصرف کاملاً سیستمی است (بدون LLM). فقط تومان/ریال در ردیابیِ هدف حساب
می‌شوند؛ ارز خارجی فعلاً کنار گذاشته می‌شود.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from bot.db import repo
from bot.services import tags as tags_service
from bot.utils import jalali
from bot.utils.money import group_digits, to_rial

logger = logging.getLogger(__name__)

_ALERTS = {
    50: "🟡 هشدار بودجه: تا الان **۵۰٪** بودجه‌ی «{topic}» برای {month} رو خرج کردی.\n"
        "({spent} از {limit} تومان)",
    80: "🟠 حواست باشه: **۸۰٪** بودجه‌ی «{topic}» برای {month} مصرف شد.\n"
        "({spent} از {limit} تومان) — تا {end} فرصت داری.",
    100: "🔴 از بودجه‌ی «{topic}» عبور کردی! ({spent} از {limit} تومان)\n"
         "طبق هدفت، تا پایان {month} ({end}) دیگه بودجه‌ای برای این نداری.",
}


def _limit_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _match_tag_id(topic: str) -> Optional[int]:
    if not topic:
        return None
    ids, _, _ = tags_service.reconcile([topic], repo.get_tags())
    return ids[0] if ids else None


def create_or_update_from_item(user_id: int, item: dict[str, Any]) -> Optional[int]:
    """هدف را از آیتم استخراج‌شده می‌سازد یا (اگر همان ماه/تگ باشد) به‌روزرسانی می‌کند."""
    topic = (item.get("topic") or "").strip() or None
    limit_amount = _limit_int(item.get("limit_amount"))
    tag_id = _match_tag_id(topic) if topic else None
    jyear, jmonth = jalali.current_ym()
    complete = bool(topic) and limit_amount is not None
    status = "active" if complete else "draft"

    existing = repo.find_goal_by_tag_month(user_id, tag_id, topic, jyear, jmonth)
    if existing:
        fields = {"status": status}
        if topic:
            fields["topic"] = topic
        if limit_amount is not None:
            fields["limit_amount"] = limit_amount
        if tag_id is not None:
            fields["tag_id"] = tag_id
        if item.get("note"):
            fields["note"] = str(item["note"]).strip()
        repo.update_goal(existing["id"], **fields)
        return existing["id"]

    return repo.create_goal(
        user_id=user_id, topic=topic, tag_id=tag_id, limit_amount=limit_amount,
        jyear=jyear, jmonth=jmonth, note=(item.get("note") or "").strip(), status=status,
    )


def apply_update(user_id: int, goal_id: int, item: dict[str, Any]) -> Optional[int]:
    goal = repo.get_goal(goal_id)
    if not goal or goal.get("user_id") != user_id:
        return None
    fields: dict[str, Any] = {}
    if item.get("topic"):
        fields["topic"] = str(item["topic"]).strip()
        tid = _match_tag_id(fields["topic"])
        fields["tag_id"] = tid
    if item.get("limit_amount") is not None:
        fields["limit_amount"] = _limit_int(item["limit_amount"])
    if item.get("note"):
        fields["note"] = str(item["note"]).strip()
    if fields:
        repo.update_goal(goal_id, **fields)
    _sync_status(goal_id)
    return goal_id


def _sync_status(goal_id: int) -> None:
    g = repo.get_goal(goal_id)
    if not g:
        return
    complete = bool((g.get("topic") or "").strip()) and g.get("limit_amount") is not None
    repo.update_goal(goal_id, status="active" if complete else "draft")


def is_complete(goal: dict[str, Any]) -> bool:
    return bool((goal.get("topic") or "").strip()) and goal.get("limit_amount") is not None


def spent_toman(user_id: int, goal: dict[str, Any]) -> int:
    """مجموعِ تومانیِ خرج‌های ماهِ هدف که به موضوع/تگِ هدف می‌خورند."""
    txns = repo.confirmed_in_jmonth(user_id, goal["jyear"], goal["jmonth"])
    if goal.get("tag_id"):
        names = repo.tag_descendant_names(goal["tag_id"])
        def matches(t):
            return bool(set(t.get("tags") or []) & names)
    else:
        topic = tags_service.normalize(goal.get("topic") or "")
        def matches(t):
            hay = tags_service.normalize((t.get("title") or "") + " " + " ".join(t.get("tags") or []))
            return bool(topic) and topic in hay
    total_rial = sum(to_rial(t.get("amount"), t.get("currency_display", "toman"))
                     for t in txns if matches(t))
    return total_rial // 10


def _level_for(pct: float) -> int:
    if pct >= 100:
        return 100
    if pct >= 80:
        return 80
    if pct >= 50:
        return 50
    return 0


async def evaluate_and_alert(bot, user_id: int) -> None:
    """بعد از هر تغییرِ مؤثر صدا زده می‌شود؛ فقط بالاترین آستانه‌ی تازه‌عبورکرده را آلارم می‌دهد."""
    jyear, jmonth = jalali.current_ym()
    for goal in repo.active_goals_for_month(user_id, jyear, jmonth):
        limit = goal.get("limit_amount") or 0
        if limit <= 0:
            continue
        spent = spent_toman(user_id, goal)
        level = _level_for(spent / limit * 100)
        if level > (goal.get("last_alert_level") or 0):
            text = _ALERTS[level].format(
                topic=goal.get("topic") or "این دسته",
                month=jalali.month_name(jmonth),
                end=jalali.month_end_str(jyear, jmonth),
                spent=group_digits(spent), limit=group_digits(limit),
            )
            try:
                await bot.send_message(chat_id=user_id, text=text)
                repo.update_goal(goal["id"], last_alert_level=level)
            except Exception:  # noqa: BLE001
                logger.warning("ارسال آلارم هدف %s ناموفق بود", goal["id"])
