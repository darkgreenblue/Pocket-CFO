"""ابزارهای فقط-خواندنی برای پاسخ به سؤال‌های دیتایی کاربر (گزارش/فهرست/بررسی).

ثبت و ویرایش تراکنش از مسیر استخراج آرایه‌ای انجام می‌شود (services/transactions)؛
این ابزارها فقط دیتا را می‌خوانند تا مدل بتواند بدون ساختنِ عدد، درباره‌اش حرف بزند.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bot.db import repo
from bot.utils.money import to_rial

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
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "جمع‌بندی هزینه‌ها در یک بازه: مجموع، تعداد و تفکیک بر اساس دسته.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "month"]},
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


def dispatch(name: str, args: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    try:
        if name == "list_transactions":
            period = args.get("period", "today")
            txns = repo.list_user_transactions(user_id, _start_iso(period))
            return {"period": period, "count": len(txns),
                    "transactions": [_txn_brief(t) for t in txns]}
        if name == "get_summary":
            period = args.get("period", "today")
            txns = repo.list_user_transactions(user_id, _start_iso(period), include_drafts=False)
            total_rial = 0
            by_cat: dict[str, int] = {}
            for t in txns:
                rial = to_rial(t.get("amount"), t.get("currency_display", "toman"))
                total_rial += rial
                cat = (t.get("tags") or ["بدون دسته"])[0]
                by_cat[cat] = by_cat.get(cat, 0) + rial
            return {"period": period, "count": len(txns), "total_toman": total_rial // 10,
                    "by_category_toman": {k: v // 10 for k, v in
                                          sorted(by_cat.items(), key=lambda x: -x[1])}}
        return {"error": f"ابزار ناشناخته: {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
