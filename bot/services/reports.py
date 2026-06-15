"""گزارش‌گیری ساده: جمع هزینه‌ها در بازه و تفکیک بر اساس تگ سطح‌بالا."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from bot.db import repo
from bot.utils.money import group_digits, to_persian_digits, to_rial


def _start_iso(period: str) -> str:
    now = datetime.now()
    delta = timedelta(days=7) if period == "week" else timedelta(days=30)
    return (now - delta).isoformat()


def build_report(user_id: int, period: str = "month") -> str:
    txns = repo.confirmed_in_range(user_id, _start_iso(period))
    label = "هفته‌ی گذشته" if period == "week" else "ماه گذشته"

    if not txns:
        return f"📊 در {label} هیچ تراکنش ثبت‌شده‌ای نداری."

    total_rial = 0
    by_category: Counter[str] = Counter()
    item_counter: Counter[str] = Counter()

    for txn in txns:
        rial = to_rial(txn.get("amount"), txn.get("currency_display", "toman"))
        total_rial += rial
        cats = txn.get("tags") or ["بدون دسته"]
        # هزینه را به تگ اول نسبت می‌دهیم (سطح کلان در گزارش خلاصه کافی است)
        by_category[cats[0]] += rial
        for name in cats:
            item_counter[name] += 1

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

    top_items = item_counter.most_common(3)
    if top_items:
        lines.append("")
        lines.append("🔁 پرتکرارترین دسته‌ها:")
        for name, count in top_items:
            lines.append(f"• {name} ({to_persian_digits(count)} بار)")

    return "\n".join(lines)
