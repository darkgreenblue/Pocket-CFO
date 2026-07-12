"""ابزارهای فقط-خواندنی/محاسباتی برای پاسخ به سؤال‌های دیتایی کاربر.

محاسبات (جمع، تبدیل ارز) را **سیستم** انجام می‌دهد، نه LLM. مدل فقط می‌فهمد چه چیزی
خواسته شده و نرخِ تبدیل را — اگر کاربر داده باشد — به ابزار می‌دهد؛ خود عددها را
نمی‌سازد و حساب نمی‌کند.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bot.db import repo
from bot.utils import jalali
from bot.utils.money import currency_label

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_transactions",
            "description": "فهرست تراکنش‌های کاربر در یک بازه (برای «امروز چی ثبت کردم؟»، بررسی ثبت تکراری و…).",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "month", "all"]},
                    "jalali_month": {"type": "string",
                                     "description": "نام ماه شمسی مثل «خرداد» برای فیلتر آن ماه (اختیاری)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": ("جمع‌بندی هزینه‌ها در یک بازه؛ مجموع را برای هر واحد پول جداگانه "
                            "برمی‌گرداند (تبدیل خودکار نمی‌کند) و تفکیک دسته‌ی تومانی را می‌دهد."),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "month"]},
                    "jalali_month": {"type": "string",
                                     "description": "نام ماه شمسی مثل «خرداد» برای جمع آن ماه (اختیاری)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_total_in_toman",
            "description": (
                "جمع کلِ هزینه‌های یک بازه را به تومان حساب می‌کند. تبدیل ارز فقط با نرخی که "
                "کاربر صریحاً داده انجام می‌شود. اگر ارز خارجی‌ای نرخش داده نشده باشد، در "
                "missing_rates برمی‌گردد؛ آن‌وقت باید نرخ را از کاربر بپرسی، نه اینکه حدس بزنی."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "month"]},
                    "rates_toman_per_unit": {
                        "type": "object",
                        "description": "نرخ هر واحد به تومان که کاربر داده، مثل {\"usdt\": 170000}",
                    },
                },
                "required": ["period"],
            },
        },
    },
]


def _start_iso(period: str) -> str | None:
    now = datetime.now()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if period == "week":
        return (now - timedelta(days=7)).isoformat()
    if period == "month":
        return (now - timedelta(days=30)).isoformat()
    return None


def _txn_brief(t: dict) -> dict:
    return {
        "id": t["id"],
        "title": t.get("title") or "(نامشخص)",
        "amount": t.get("amount"),
        "currency": t.get("currency_display"),
        "status": t.get("status"),
        "tags": t.get("tags") or [],
        "date": (t.get("created_at") or "")[:10],
    }


def _by_currency(txns: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in txns:
        amt = t.get("amount")
        if amt is None:
            continue
        cur = t.get("currency_display", "toman")
        out[cur] = out.get(cur, 0) + amt
    return out


def _txns_for(user_id: int, args: dict, include_drafts: bool) -> tuple[str, list[dict]]:
    """(برچسبِ بازه، تراکنش‌ها) — بر اساس ماه شمسی یا بازه‌ی نسبی."""
    jm = (args.get("jalali_month") or "").strip()
    if jm:
        resolved = jalali.resolve_past_month(jm)
        if resolved:
            y, m = resolved
            return f"{jalali.month_name(m)} {y}", repo.confirmed_in_jmonth(user_id, y, m)
    period = args.get("period", "today")
    return period, repo.list_user_transactions(user_id, _start_iso(period),
                                               include_drafts=include_drafts)


def dispatch(name: str, args: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    try:
        if name == "list_transactions":
            label, txns = _txns_for(user_id, args, include_drafts=True)
            return {"period": label, "count": len(txns),
                    "transactions": [_txn_brief(t) for t in txns]}

        if name == "get_summary":
            label, txns = _txns_for(user_id, args, include_drafts=False)
            by_cur = _by_currency(txns)
            cats: dict[str, float] = {}
            for t in txns:
                if t.get("currency_display", "toman") != "toman" or t.get("amount") is None:
                    continue
                cat = (t.get("tags") or ["بدون دسته"])[0]
                cats[cat] = cats.get(cat, 0) + t["amount"]
            return {
                "period": label, "count": len(txns),
                "totals_by_currency": {currency_label(c): v for c, v in by_cur.items()},
                "toman_by_category": dict(sorted(cats.items(), key=lambda x: -x[1])),
                "note": "ارزها جدا هستند و تبدیل خودکار انجام نشده.",
            }

        if name == "compute_total_in_toman":
            period = args.get("period", "today")
            rates = {str(k).lower(): float(v) for k, v in
                     (args.get("rates_toman_per_unit") or {}).items()}
            txns = repo.list_user_transactions(user_id, _start_iso(period), include_drafts=False)
            by_cur = _by_currency(txns)
            total = 0.0
            missing = []
            for cur, amt in by_cur.items():
                if cur == "toman":
                    total += amt
                elif cur == "rial":
                    total += amt / 10
                elif cur in rates:
                    total += amt * rates[cur]
                else:
                    missing.append(currency_label(cur))
            return {
                "period": period,
                "grand_total_toman": int(total) if not missing else None,
                "missing_rates": missing,
                "used_rates": rates,
            }

        return {"error": f"ابزار ناشناخته: {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
