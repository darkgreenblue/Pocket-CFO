"""تصویرِ فشرده‌ی دفتر برای مدل — «چه چیزهایی الان وجود دارد».

تا قبل از این، مدل فقط **متنِ گفتگو** را می‌دید، نه خودِ دیتا را. پس وقتی کاربر می‌گفت
«اون ۸۶ تومنی آب معدنی بود»، مدل راهی نداشت بفهمد منظور تراکنشِ #474 است؛ فقط اگر کاربر
دقیقاً روی کارت ریپلای می‌زد شماره به دستش می‌رسید. یک مدیرِ مالیِ واقعی دفترش جلوی
چشمش است — این همان دفتر است.

همه‌چیز صریحاً «قبلاً ثبت‌شده» برچسب می‌خورد تا مدل آن‌ها را دوباره نسازد؛ این فهرست
برای ارجاع و اصلاح است، نه برای استخراج.
"""
from __future__ import annotations

import logging
from datetime import datetime

from bot.db import repo
from bot.services import debts as debts_service
from bot.services import goals as goals_service
from bot.services import household as household_service
from bot.utils import jalali
from bot.utils.money import format_amount, group_digits, to_persian_digits

logger = logging.getLogger(__name__)

MAX_TODAY = 20
MAX_DRAFTS = 10
MAX_DEBTS = 10

HEADER = (
    "📒 وضعیتِ فعلیِ دفتر (مستقیم از دیتابیس). همه‌ی این‌ها **قبلاً ثبت شده‌اند** — هرگز "
    "دوباره نسازشان. این فهرست برای این است که بفهمی کاربر به کدام مورد اشاره می‌کند "
    "(«همون ۸۶ تومنی»، «اون آب معدنی»، «خرجِ رستورانِ امروز») و با **همین شماره‌ها** "
    "اصلاح/حذفش کنی. اگر چند مورد با حرفِ کاربر جور درمی‌آید، حدس نزن؛ در reply بپرس کدام."
)


def _start_of_today_iso() -> str:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _txn_line(txn: dict, names: dict[int, str] | None) -> str:
    title = (txn.get("title") or "").strip() or "(بی‌عنوان)"
    parts = [f"#{txn['id']} {title}",
             format_amount(txn.get("amount"), txn.get("currency_display", "toman"))]
    items = txn.get("mentioned_items") or []
    if items:
        parts.append("اقلام: " + "، ".join(str(i) for i in items))
    if (txn.get("note") or "").strip():
        parts.append("توضیح: " + str(txn["note"]).strip()[:80])
    if names:
        parts.append(names.get(txn.get("user_id"), household_service.DEFAULT_NAME))
    return "• " + " — ".join(parts)


def snapshot(user_id: int) -> str:
    """متنِ دفتر برای پرامپت. اگر هیچ‌چیز نباشد، رشته‌ی خالی (پرامپت شلوغ نمی‌شود)."""
    try:
        return _snapshot(user_id)
    except Exception:  # noqa: BLE001 — نبودِ دفتر نباید جلوی جواب‌دادن را بگیرد
        logger.exception("ساختِ تصویرِ دفتر ناموفق بود")
        return ""


def _snapshot(user_id: int) -> str:
    names = household_service.name_map(user_id) if household_service.is_shared(user_id) else None
    sections: list[str] = []

    today = repo.list_user_transactions(user_id, _start_of_today_iso(), include_drafts=False)
    if today:
        lines = [_txn_line(t, names) for t in today[:MAX_TODAY]]
        if len(today) > MAX_TODAY:
            lines.append(f"• … و {to_persian_digits(len(today) - MAX_TODAY)} مورد دیگر")
        sections.append("تراکنش‌های امروز:\n" + "\n".join(lines))

    drafts = repo.household_drafts(user_id, MAX_DRAFTS)
    if drafts:
        lines = []
        for t in drafts:
            missing = []
            if not (t.get("title") or "").strip():
                missing.append("عنوان")
            if t.get("amount") is None:
                missing.append("مبلغ")
            tail = f" — ناقص: {' و '.join(missing)}" if missing else ""
            lines.append(_txn_line(t, names) + tail)
        sections.append("تراکنش‌های ناقص (منتظرِ تکمیل):\n" + "\n".join(lines))

    jyear, jmonth = jalali.current_ym()
    goal_lines = []
    for goal in repo.active_goals_for_month(user_id, jyear, jmonth):
        limit = goal.get("limit_amount") or 0
        if limit <= 0:
            continue
        spent = goals_service.spent_toman(user_id, goal)
        pct = int(spent * 100 / limit)
        goal_lines.append(
            f"• {goal.get('topic')}: {group_digits(spent)} از {group_digits(limit)} تومان "
            f"({to_persian_digits(pct)}٪) — مانده {group_digits(max(limit - spent, 0))}"
        )
    if goal_lines:
        sections.append(f"اهدافِ {jalali.month_name(jmonth)}:\n" + "\n".join(goal_lines))

    debt_lines = []
    for debt in repo.list_debts(user_id)[:MAX_DEBTS]:
        kind = debts_service.KIND_LABELS.get(debts_service.normalize_kind(debt.get("kind")), "بدهی")
        party = debt.get("counterparty") or "(نامشخص)"
        rest = debts_service.remaining(debt)
        debt_lines.append(
            f"• {kind} #{debt['id']} — {party} — مانده "
            f"{format_amount(rest, debt.get('currency_display', 'toman'))}"
        )
    if debt_lines:
        sections.append("بدهی/طلب‌های باز (شماره‌ها مالِ جدولِ بدهی‌اند، نه تراکنش):\n"
                        + "\n".join(debt_lines))

    if not sections:
        return ""
    return HEADER + "\n\n" + "\n\n".join(sections)
