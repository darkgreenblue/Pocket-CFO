"""ابزارهای (function-calling) که LLM می‌تواند برای کار با دیتای کاربر صدا بزند.

هر ابزار روی دیتابیس عمل می‌کند و نتیجه را به‌صورت متن/JSON برمی‌گرداند تا مدل
بتواند درباره‌اش حرف بزند. ساختِ تراکنش، اثرش (ساخت/ویرایش کارت) در ``effects``
جمع می‌شود تا هندلر بعد از پاسخ، کارت‌ها را در تلگرام بفرستد/به‌روزرسانی کند.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from bot.config import settings
from bot.db import repo
from bot.services import tags as tags_service
from bot.utils.money import to_rial

KNOWN_CURRENCIES = ["toman", "rial", "usd", "eur", "usdt", "btc", "aed", "try"]

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "record_expense",
            "description": (
                "ثبت یک «هزینه‌کرد» (یک بار پرداخت). برای هر پرداخت جدا یک‌بار صدا بزن. "
                "اقلام چند قلمی یک پرداخت در mentioned_items می‌روند، نه تراکنش جدا."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": ["string", "null"],
                              "description": "عنوان کوتاه و واقعی. اگر کاربر نگفت null بگذار؛ از خودت «متفرقه» نساز."},
                    "amount": {"type": ["integer", "null"],
                               "description": "مبلغ صحیح. اگر گفته نشد null."},
                    "currency": {"type": ["string", "null"], "enum": KNOWN_CURRENCIES + [None],
                                 "description": "فقط اگر کاربر واحد را صریح گفت؛ وگرنه null."},
                    "mentioned_items": {"type": "array", "items": {"type": "string"}},
                    "suggested_tags": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "needs_later_completion": {"type": "boolean"},
                    "title_unimportant": {"type": "boolean",
                                          "description": "فقط اگر کاربر صراحتاً گفت عنوان مهم نیست."},
                },
                "required": ["amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_transaction",
            "description": "اصلاح یک تراکنش موجود (مثلاً وقتی کاربر مبلغ/عنوان فراموش‌شده را بعداً می‌گوید یا به کارت ریپلای می‌کند).",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "amount": {"type": "integer"},
                    "currency": {"type": "string", "enum": KNOWN_CURRENCIES},
                    "note": {"type": "string"},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_transactions",
            "description": "فهرست تراکنش‌های کاربر در یک بازه برای پاسخ به سؤال‌ها (مثل «امروز چی ثبت کردم؟» یا بررسی ثبت تکراری).",
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


def _normalize_currency(value: Any) -> str | None:
    if isinstance(value, str) and value.lower() in KNOWN_CURRENCIES:
        return value.lower()
    return None


def dispatch(name: str, args: dict[str, Any], *, user_id: int, effects: dict[str, list]) -> dict[str, Any]:
    """یک tool_call را اجرا می‌کند و نتیجه‌ی قابل‌خواندن برای مدل برمی‌گرداند."""
    try:
        if name == "record_expense":
            return _record_expense(args, user_id, effects)
        if name == "update_transaction":
            return _update_transaction(args, user_id, effects)
        if name == "list_transactions":
            return _list_transactions(args, user_id)
        if name == "get_summary":
            return _get_summary(args, user_id)
        return {"error": f"ابزار ناشناخته: {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _apply_tags(txn_id: int, suggested: list[str], user_id: int) -> list[str]:
    tag_bank = repo.get_tags()
    matched_ids, matched_names, unmatched = tags_service.reconcile(suggested or [], tag_bank)
    for tid in matched_ids:
        repo.add_transaction_tag(txn_id, tid)
    for nm in unmatched:
        repo.add_tag_suggestion(user_id, txn_id, nm)
    return matched_names


def _record_expense(args: dict, user_id: int, effects: dict) -> dict:
    title = (args.get("title") or "").strip() or None
    if title is None and args.get("title_unimportant"):
        title = "بدون عنوان"
    amount = args.get("amount")
    amount = int(amount) if amount is not None else None
    currency = _normalize_currency(args.get("currency")) or settings.default_currency
    complete = amount is not None and bool(title)
    status = "confirmed" if complete else "draft"

    txn_id = repo.create_transaction(
        user_id=user_id, title=title, amount=amount, currency_display=currency,
        note=(args.get("note") or "").strip(),
        mentioned_items=[str(x) for x in (args.get("mentioned_items") or [])],
        needs_later_completion=bool(args.get("needs_later_completion", False)),
        transcript="", source="chat", status=status,
    )
    _apply_tags(txn_id, args.get("suggested_tags") or [], user_id)
    effects["created"].append(txn_id)

    missing = [m for m, ok in (("عنوان", bool(title)), ("مبلغ", amount is not None)) if not ok]
    return {"transaction_id": txn_id, "status": status, "missing_fields": missing}


def _update_transaction(args: dict, user_id: int, effects: dict) -> dict:
    txn_id = args.get("transaction_id")
    txn = repo.get_transaction(txn_id) if txn_id else None
    if not txn or txn["user_id"] != user_id:
        return {"error": "تراکنش پیدا نشد."}
    fields: dict[str, Any] = {}
    if "title" in args and args["title"]:
        fields["title"] = str(args["title"]).strip()
    if "amount" in args and args["amount"] is not None:
        fields["amount"] = int(args["amount"])
    if args.get("currency"):
        cur = _normalize_currency(args["currency"])
        if cur:
            fields["currency_display"] = cur
    if "note" in args and args["note"]:
        fields["note"] = str(args["note"]).strip()
    if fields:
        repo.update_transaction(txn_id, **fields)
    status = repo.sync_status(txn_id)
    effects["updated"].append(txn_id)
    return {"transaction_id": txn_id, "status": status}


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


def _list_transactions(args: dict, user_id: int) -> dict:
    period = args.get("period", "today")
    txns = repo.list_user_transactions(user_id, _start_iso(period))
    return {"period": period, "count": len(txns), "transactions": [_txn_brief(t) for t in txns]}


def _get_summary(args: dict, user_id: int) -> dict:
    period = args.get("period", "today")
    txns = repo.list_user_transactions(user_id, _start_iso(period), include_drafts=False)
    total_rial = 0
    by_cat: dict[str, int] = {}
    for t in txns:
        rial = to_rial(t.get("amount"), t.get("currency_display", "toman"))
        total_rial += rial
        cat = (t.get("tags") or ["بدون دسته"])[0]
        by_cat[cat] = by_cat.get(cat, 0) + rial
    return {
        "period": period,
        "count": len(txns),
        "total_toman": total_rial // 10,
        "by_category_toman": {k: v // 10 for k, v in sorted(by_cat.items(), key=lambda x: -x[1])},
    }
