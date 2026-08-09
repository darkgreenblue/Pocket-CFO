"""گزارش‌گیری: جمع هزینه‌های خانوار در بازه، تفکیک تگ، تفکیک ثبت‌کننده و بدهی/طلب.

گزارش همیشه در سطحِ خانوار است (عملکردِ تجمعی)؛ فقط وقتی خانوار بیش از یک عضو دارد،
بخشِ «چه کسی ثبت کرده» هم اضافه می‌شود.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from bot.db import repo
from bot.services import debts as debts_service
from bot.services import household as household_service
from bot.utils.money import format_amount, group_digits, to_persian_digits, to_rial


def _start_iso(period: str) -> str:
    now = datetime.now()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    delta = timedelta(days=7) if period == "week" else timedelta(days=30)
    return (now - delta).isoformat()


_LABELS = {"today": "امروز", "week": "هفته‌ی گذشته", "month": "ماه گذشته"}


def _debts_section(user_id: int) -> list[str]:
    """خطوطِ «بدهی و طلبِ باز» — فقط اگر چیزی باز باشد."""
    totals = debts_service.totals(user_id)
    lines: list[str] = []
    for kind, title in ((debts_service.DEBT, "🔻 بدهی‌های باز"),
                        (debts_service.CREDIT, "🔺 طلب‌های باز")):
        by_currency = totals.get(kind) or {}
        if not by_currency:
            continue
        parts = [format_amount(v, c) for c, v in by_currency.items()]
        lines.append(f"{title}: {' + '.join(parts)}")
    return lines


def build_report(user_id: int, period: str = "month") -> str:
    txns = repo.confirmed_in_range(user_id, _start_iso(period))
    label = _LABELS.get(period, "ماه گذشته")
    debt_lines = _debts_section(user_id)

    if not txns:
        head = f"📊 در {label} هیچ تراکنش ثبت‌شده‌ای نداری."
        return "\n\n".join([head, "\n".join(debt_lines)]) if debt_lines else head

    total_rial = 0
    by_category: Counter[str] = Counter()
    item_counter: Counter[str] = Counter()
    by_member_rial: Counter[int] = Counter()
    by_member_count: Counter[int] = Counter()

    for txn in txns:
        rial = to_rial(txn.get("amount"), txn.get("currency_display", "toman"))
        total_rial += rial
        cats = txn.get("tags") or ["بدون دسته"]
        # هزینه را به تگ اول نسبت می‌دهیم (سطح کلان در گزارش خلاصه کافی است)
        by_category[cats[0]] += rial
        for name in cats:
            item_counter[name] += 1
        by_member_rial[txn.get("user_id")] += rial
        by_member_count[txn.get("user_id")] += 1

    total_toman = total_rial // 10
    lines = [
        f"📊 گزارش {label}",
        f"تعداد تراکنش: {to_persian_digits(len(txns))}",
        f"مجموع خرج: {group_digits(total_toman)} تومان",
        "",
        "🏷 تفکیک بر اساس دسته:",
    ]
    for name, rial in by_category.most_common():
        lines.append(f"• {name}: {group_digits(rial // 10)} تومان")

    # تفکیکِ ثبت‌کننده فقط وقتی معنا دارد که خانوار بیش از یک عضو داشته باشد.
    if household_service.is_shared(user_id):
        names = household_service.name_map(user_id)
        lines.append("")
        lines.append("👥 به تفکیک ثبت‌کننده:")
        for member_id, rial in by_member_rial.most_common():
            name = names.get(member_id, household_service.DEFAULT_NAME)
            count = to_persian_digits(by_member_count[member_id])
            lines.append(f"• {name}: {group_digits(rial // 10)} تومان ({count} تراکنش)")

    top_items = item_counter.most_common(3)
    if top_items:
        lines.append("")
        lines.append("🔁 پرتکرارترین دسته‌ها:")
        for name, count in top_items:
            lines.append(f"• {name} ({to_persian_digits(count)} بار)")

    if debt_lines:
        lines.append("")
        lines += debt_lines

    return "\n".join(lines)
