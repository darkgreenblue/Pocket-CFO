"""ابزارهای فقط-خواندنی/محاسباتی برای پاسخ به سؤال‌های دیتایی کاربر.

محاسبات (جمع، تبدیل ارز) را **سیستم** انجام می‌دهد، نه LLM. مدل فقط می‌فهمد چه چیزی
خواسته شده و نرخِ تبدیل را — اگر کاربر داده باشد — به ابزار می‌دهد؛ خود عددها را
نمی‌سازد و حساب نمی‌کند.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bot.db import repo
from bot.services import debts as debts_service
from bot.services import household as household_service
from bot.services import tags as tags_service
from bot.utils import jalali
from bot.utils.money import currency_label

# برچسبِ مجازیِ «تراکنش‌های بدونِ تگ». تگِ واقعی نیست؛ هم در get_summary و هم در
# فیلترِ دسته باید یکسان تفسیر شود، وگرنه «دیتیلِ بدون‌دسته» چیزی پیدا نمی‌کند.
UNCATEGORIZED = "بدون دسته"
_UNCATEGORIZED_ALIASES = {"بدون دسته", "بی دسته", "بدون دسته بندی",
                          "دسته بندی نشده", "بدون تگ", "متفرقه"}

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_transactions",
            "description": ("فهرستِ تک‌تکِ تراکنش‌های کاربر در یک بازه — برای «امروز چی ثبت کردم؟»، "
                            "بررسی ثبت تکراری، و «جزئیات/لیستِ یک دسته» (مثلاً همه‌ی خرج‌های «هدیه»ی "
                            "خرداد). برای دیدنِ ریزِ یک دسته، پارامتر category را بده."),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "month", "all"]},
                    "jalali_month": {"type": "string",
                                     "description": "نام ماه شمسی مثل «خرداد» برای فیلتر آن ماه (اختیاری)."},
                    "category": {"type": "string",
                                 "description": ("نام دسته/تگ برای فیلترِ ریزِ همان دسته (مثل «هدیه» یا "
                                                 "«رستوران»). زیرشاخه‌ها هم شامل می‌شوند. برای دیدنِ "
                                                 "تراکنش‌های بدونِ تگ، «بدون دسته» را بده. اختیاری.")},
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
            "name": "list_debts",
            "description": ("فهرست بدهی‌ها و طلب‌های خانوار با مانده‌ی تسویه‌نشده‌ی هرکدام — "
                            "برای «چقدر بدهکارم؟»، «کی بهم بدهکاره؟»، «بدهیم به رضا چقدر مونده؟»."),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["debt", "credit", "all"],
                             "description": ("debt = بدهی‌های خودمان، credit = طلب‌هایمان از "
                                             "دیگران، all = هر دو (پیش‌فرض).")},
                    "include_settled": {"type": "boolean",
                                        "description": "تسویه‌شده‌ها هم بیایند؟ پیش‌فرض false."},
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


def _txn_brief(t: dict, names: dict[int, str] | None = None) -> dict:
    brief = {
        "id": t["id"],
        "title": t.get("title") or "(نامشخص)",
        "amount": t.get("amount"),
        "currency": t.get("currency_display"),
        "status": t.get("status"),
        "tags": t.get("tags") or [],
        "date": (t.get("created_at") or "")[:10],
    }
    # فقط در خانوارِ چندنفره معنا دارد؛ در حالت تک‌نفره نویزِ اضافه است.
    if names:
        brief["recorded_by"] = names.get(t.get("user_id"), household_service.DEFAULT_NAME)
    return brief


def _debt_brief(d: dict, names: dict[int, str] | None = None) -> dict:
    brief = {
        "id": d["id"],
        "kind": d.get("kind"),
        "kind_fa": debts_service.KIND_LABELS.get(d.get("kind"), "بدهی"),
        "counterparty": d.get("counterparty") or "(نامشخص)",
        "title": d.get("title") or "(نامشخص)",
        "amount": d.get("amount"),
        "settled_amount": d.get("settled_amount") or 0,
        "remaining": debts_service.remaining(d),
        "currency": d.get("currency_display"),
        "status": d.get("status"),
        "due": d.get("due_text"),
        "date": (d.get("created_at") or "")[:10],
    }
    if names:
        brief["recorded_by"] = names.get(d.get("user_id"), household_service.DEFAULT_NAME)
    return brief


def _member_names(user_id: int) -> dict[int, str] | None:
    return household_service.name_map(user_id) if household_service.is_shared(user_id) else None


def _by_currency(txns: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in txns:
        amt = t.get("amount")
        if amt is None:
            continue
        cur = t.get("currency_display", "toman")
        out[cur] = out.get(cur, 0) + amt
    return out


def _filter_by_category(txns: list[dict], category: str) -> list[dict]:
    """فقط تراکنش‌هایی که به دسته/تگِ خواسته‌شده (و زیرشاخه‌هایش) می‌خورند.

    مثل موتور اهداف: اول تگ را تطبیق می‌دهیم و بر اساس نام‌های زیرشاخه فیلتر می‌کنیم؛
    اگر تگ تطبیق نخورد، به تطبیقِ متنی روی عنوان/تگ برمی‌گردیم.
    """
    category = (category or "").strip()
    if not category:
        return txns
    # «بدون دسته» = تراکنش‌های بدونِ هیچ تگ (برچسبِ مجازی، نه تگِ واقعی).
    if tags_service.normalize(category) in {tags_service.normalize(a) for a in _UNCATEGORIZED_ALIASES}:
        return [t for t in txns if not (t.get("tags") or [])]
    ids, _, _ = tags_service.reconcile([category], repo.get_tags())
    if ids:
        names = repo.tag_descendant_names(ids[0])
        return [t for t in txns if set(t.get("tags") or []) & names]
    norm = tags_service.normalize(category)
    out = []
    for t in txns:
        hay = tags_service.normalize((t.get("title") or "") + " " + " ".join(t.get("tags") or []))
        if norm and norm in hay:
            out.append(t)
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
            category = (args.get("category") or "").strip()
            if category:
                txns = _filter_by_category(txns, category)
            names = _member_names(user_id)
            out = {"period": label, "count": len(txns),
                   "transactions": [_txn_brief(t, names) for t in txns]}
            if category:
                out["category"] = category
                out["totals_by_currency"] = {
                    currency_label(c): v for c, v in _by_currency(txns).items()}
            return out

        if name == "get_summary":
            label, txns = _txns_for(user_id, args, include_drafts=False)
            by_cur = _by_currency(txns)
            cats: dict[str, float] = {}
            for t in txns:
                if t.get("currency_display", "toman") != "toman" or t.get("amount") is None:
                    continue
                cat = (t.get("tags") or [UNCATEGORIZED])[0]
                cats[cat] = cats.get(cat, 0) + t["amount"]
            return {
                "period": label, "count": len(txns),
                "totals_by_currency": {currency_label(c): v for c, v in by_cur.items()},
                "toman_by_category": dict(sorted(cats.items(), key=lambda x: -x[1])),
                "note": "ارزها جدا هستند و تبدیل خودکار انجام نشده.",
            }

        if name == "list_debts":
            kind = (args.get("kind") or "all").strip().lower()
            rows = repo.list_debts(
                user_id,
                kind=None if kind not in ("debt", "credit") else kind,
                include_settled=bool(args.get("include_settled")),
            )
            names = _member_names(user_id)
            open_totals = debts_service.totals(user_id)
            return {
                "count": len(rows),
                "debts": [_debt_brief(d, names) for d in rows],
                "open_totals": {
                    "بدهی": {currency_label(c): v
                             for c, v in (open_totals[debts_service.DEBT]).items()},
                    "طلب": {currency_label(c): v
                            for c, v in (open_totals[debts_service.CREDIT]).items()},
                },
                "note": "remaining یعنی مانده‌ی تسویه‌نشده. ارزها جدا هستند.",
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
