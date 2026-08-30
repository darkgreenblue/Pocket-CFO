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
    # ارزِ خارجی به تومان تبدیل نمی‌شود (نرخ را حدس نمی‌زنیم)، پس جدا جمع می‌شود.
    # قبلاً چون to_rial برای ارز خارجی صفر می‌دهد، این خرج‌ها اصلاً در گزارش دیده نمی‌شدند.
    foreign: dict[str, float] = {}
    foreign_count = 0

    for txn in txns:
        currency = (txn.get("currency_display") or "toman").lower()
        amount = txn.get("amount")
        cats = txn.get("tags") or ["بدون دسته"]
        for name in cats:
            item_counter[name] += 1
        by_member_count[txn.get("user_id")] += 1

        if currency in ("toman", "rial"):
            rial = to_rial(amount, currency)
            total_rial += rial
            # هزینه را به تگ اول نسبت می‌دهیم (سطح کلان در گزارش خلاصه کافی است)
            by_category[cats[0]] += rial
            by_member_rial[txn.get("user_id")] += rial
        elif amount is not None:
            foreign[currency] = foreign.get(currency, 0) + amount
            foreign_count += 1

    total_toman = total_rial // 10
    lines = [
        f"📊 گزارش {label}",
        f"تعداد تراکنش: {to_persian_digits(len(txns))}",
    ]
    if total_rial or not foreign:
        lines.append(f"مجموع خرج: {group_digits(total_toman)} تومان")
    if foreign:
        parts = "، ".join(format_amount(v, c) for c, v in foreign.items())
        lines.append(f"💵 ارزهای دیگر: {parts}")
        lines.append("   (تبدیل خودکار نمی‌کنیم؛ اگر نرخ را بگویی جمعِ تومانی را حساب می‌کنم.)")

    if by_category:
        lines.append("")
        lines.append("🏷 تفکیک بر اساس دسته:")
        for name, rial in by_category.most_common():
            lines.append(f"• {name}: {group_digits(rial // 10)} تومان")
        if foreign_count:
            lines.append(f"   ({to_persian_digits(foreign_count)} تراکنشِ ارزی در این تفکیک "
                         "نیست چون واحدشان فرق دارد.)")

    # تفکیکِ ثبت‌کننده فقط وقتی معنا دارد که خانوار بیش از یک عضو داشته باشد.
    if household_service.is_shared(user_id):
        names = household_service.name_map(user_id)
        lines.append("")
        lines.append("👥 به تفکیک ثبت‌کننده:")
        for member_id, count in by_member_count.most_common():
            name = names.get(member_id, household_service.DEFAULT_NAME)
            rial = by_member_rial.get(member_id, 0)
            count_fa = to_persian_digits(count)
            if rial:
                lines.append(f"• {name}: {group_digits(rial // 10)} تومان ({count_fa} تراکنش)")
            else:
                lines.append(f"• {name}: {count_fa} تراکنش (فقط ارز خارجی)")

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


# ─────────────────────────── ریزِ تراکنش‌ها ───────────────────────────
# گزارشِ بالا خلاصه است؛ این یکی تک‌تکِ کارت‌ها را با همه‌ی جزئیاتشان می‌دهد.

SCOPE_ALL = "all"
TELEGRAM_SAFE_CHARS = 3500      # سقفِ پیامِ تلگرام ۴۰۹۶ است؛ حاشیه نگه می‌داریم


def _jalali_date(iso: str) -> str:
    """تاریخِ ISO را به شمسیِ خوانا برمی‌گرداند (پروژه تقویمِ شمسی دارد)."""
    try:
        import jdatetime
        g = datetime.fromisoformat(iso).date()
        jd = jdatetime.date.fromgregorian(date=g)
        return f"{to_persian_digits(jd.year)}/{to_persian_digits(jd.month)}/" \
               f"{to_persian_digits(jd.day)}"
    except Exception:  # noqa: BLE001
        return ""


def scope_label(user_id: int, scope: str) -> str:
    if scope == SCOPE_ALL:
        return "کلِ خانوار"
    names = household_service.name_map(user_id)
    try:
        return names.get(int(scope), household_service.DEFAULT_NAME)
    except (TypeError, ValueError):
        return household_service.DEFAULT_NAME


def _txn_block(txn: dict, names: dict[int, str] | None) -> list[str]:
    """یک تراکنش با همه‌ی چیزی که درباره‌اش داریم."""
    lines = [
        f"📝 {(txn.get('title') or '').strip() or '— (نامشخص)'}",
        f"💰 {format_amount(txn.get('amount'), txn.get('currency_display', 'toman'))}",
    ]
    tags = txn.get("tags") or []
    if tags:
        lines.append("🏷 " + "، ".join(tags))
    items = txn.get("mentioned_items") or []
    if items:
        lines.append("🧺 " + "، ".join(items))
    if txn.get("note"):
        lines.append("🗒 " + str(txn["note"]).strip())
    if (txn.get("transcript") or "").strip():
        heard = " ".join(str(txn["transcript"]).split())
        lines.append(f"🎙 {heard[:150]}{'…' if len(heard) > 150 else ''}")

    footer = []
    if names:
        footer.append(names.get(txn.get("user_id"), household_service.DEFAULT_NAME))
    date = _jalali_date(txn.get("created_at") or "")
    if date:
        footer.append(date)
    if txn.get("status") == "draft":
        footer.append("ناقص")
    if footer:
        lines.append("👤 " + " · ".join(footer))
    return lines


def build_detail(user_id: int, period: str = "month", scope: str = SCOPE_ALL) -> str:
    """فهرستِ کاملِ تراکنش‌های بازه — نه خلاصه، تک‌تکِ کارت‌ها با جزئیات.

    scope: «all» برای کلِ خانوار، یا آی‌دی عددیِ یک عضو (به‌صورت رشته).
    """
    txns = repo.confirmed_in_range(user_id, _start_iso(period))
    if scope != SCOPE_ALL:
        try:
            member_id = int(scope)
        except (TypeError, ValueError):
            member_id = user_id
        txns = [t for t in txns if t.get("user_id") == member_id]

    label = _LABELS.get(period, "ماه گذشته")
    who = scope_label(user_id, scope)
    if not txns:
        return f"🧾 ریز تراکنش‌های {label} — {who}\nچیزی ثبت نشده."

    names = household_service.name_map(user_id) if household_service.is_shared(user_id) else None
    total_rial = sum(to_rial(t.get("amount"), t.get("currency_display", "toman")) for t in txns)
    foreign: dict[str, float] = {}
    for t in txns:
        currency = (t.get("currency_display") or "toman").lower()
        if currency not in ("toman", "rial") and t.get("amount") is not None:
            foreign[currency] = foreign.get(currency, 0) + t["amount"]

    head = [f"🧾 ریز تراکنش‌های {label} — {who}",
            f"تعداد: {to_persian_digits(len(txns))} · "
            f"جمع: {group_digits(total_rial // 10)} تومان"]
    if foreign:
        head.append("💵 " + "، ".join(format_amount(v, c) for c, v in foreign.items()))

    blocks = [ "\n".join(_txn_block(t, names)) for t in
               sorted(txns, key=lambda t: t.get("created_at") or "", reverse=True) ]
    return "\n".join(head) + "\n" + "─" * 12 + "\n" + ("\n" + "─" * 12 + "\n").join(blocks)


def detail_filename(period: str, scope: str) -> str:
    return f"transactions-{period}-{scope}.txt"
