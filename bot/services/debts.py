"""بدهی و طلب: ساخت از آیتمِ استخراج‌شده، تسویه (کامل/جزئی) و جمع‌بندی.

— kind = "debt"   → من/خانوار به کسی بدهکاریم.
— kind = "credit" → کسی به من/خانوار بدهکار است (طلب).

فیلدهای اجباری برای «کامل» بودن: مبلغ و طرفِ حساب. تا وقتی یکی از این‌ها نباشد،
رکورد draft می‌ماند و کارت خطِ «نیازمند تکمیل» می‌گیرد — دقیقاً مثل تراکنش و هدف.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from bot.config import settings
from bot.db import repo
from bot.services.transactions import coerce_amount, normalize_currency
from bot.utils import jalali
from bot.utils.money import to_rial

logger = logging.getLogger(__name__)

DEBT = "debt"
CREDIT = "credit"

KIND_LABELS = {DEBT: "بدهی", CREDIT: "طلب"}
KIND_EMOJI = {DEBT: "🔻", CREDIT: "🔺"}

_CREDIT_WORDS = {"credit", "receivable", "طلب", "طلبکار", "دریافتنی"}


def normalize_kind(value: Any) -> str:
    """هر چیزی که مدل داده را به debt/credit تبدیل می‌کند (پیش‌فرض: بدهی)."""
    s = str(value or "").strip().lower()
    return CREDIT if s in _CREDIT_WORDS else DEBT


def is_complete(debt: dict[str, Any]) -> bool:
    return debt.get("amount") is not None and bool((debt.get("counterparty") or "").strip())


def remaining(debt: dict[str, Any]) -> Optional[float]:
    """مانده‌ی تسویه‌نشده؛ None اگر مبلغ هنوز مشخص نیست."""
    if debt.get("amount") is None:
        return None
    rest = debt["amount"] - (debt.get("settled_amount") or 0)
    return max(rest, 0)


def _status_for(debt: dict[str, Any]) -> str:
    if not is_complete(debt):
        return "draft"
    rest = remaining(debt)
    return "settled" if rest is not None and rest <= 0 else "open"


def sync_status(debt_id: int) -> str:
    debt = repo.get_debt(debt_id)
    if not debt:
        return "deleted"
    status = _status_for(debt)
    if status != debt["status"]:
        repo.update_debt(
            debt_id, status=status,
            settled_at=datetime.now().isoformat() if status == "settled" else None,
        )
    return status


def _default_title(kind: str, counterparty: Optional[str]) -> Optional[str]:
    party = (counterparty or "").strip()
    if not party:
        return None
    return f"{KIND_LABELS[kind]} {party}"


def create_or_update_from_item(user_id: int, item: dict[str, Any]) -> tuple[Optional[int], bool]:
    """از آیتمِ استخراج‌شده یک بدهی/طلب می‌سازد. خروجی: (id، آیا تازه ساخته شد).

    اگر پیام درباره‌ی تسویه‌ی یک بدهیِ بازِ همان طرف‌حساب باشد، به‌جای رکوردِ تکراری،
    همان رکورد به‌روزرسانی می‌شود.
    """
    kind = normalize_kind(item.get("kind"))
    counterparty = (item.get("counterparty") or "").strip() or None
    amount = coerce_amount(item.get("amount"))
    currency = normalize_currency(item.get("currency")) or settings.default_currency
    settle_amount = coerce_amount(item.get("settle_amount"))
    settled_flag = bool(item.get("settled"))

    # تسویه‌ی یک بدهیِ موجود → همان رکورد، نه رکوردِ جدید.
    if (settled_flag or settle_amount is not None) and counterparty:
        existing = repo.find_open_debt(user_id, kind, counterparty)
        if existing:
            apply_update(user_id, existing["id"], item)
            return existing["id"], False

    title = (item.get("title") or "").strip() or _default_title(kind, counterparty)
    jyear, jmonth = jalali.current_ym()
    debt_id = repo.create_debt(
        user_id=user_id, kind=kind, counterparty=counterparty, title=title,
        amount=amount, currency_display=currency,
        due_text=(item.get("due_text") or "").strip() or None,
        note=(item.get("note") or "").strip(), jyear=jyear, jmonth=jmonth,
    )
    if settled_flag and amount is not None:
        repo.update_debt(debt_id, settled_amount=amount)
    elif settle_amount is not None:
        repo.update_debt(debt_id, settled_amount=settle_amount)
    sync_status(debt_id)
    return debt_id, True


def apply_update(user_id: int, debt_id: int, item: dict[str, Any]) -> Optional[int]:
    """ویرایش/تسویه‌ی یک بدهی یا طلبِ موجود در همان خانوار."""
    debt = repo.get_debt(debt_id)
    if not debt or debt.get("household_id") != repo.household_id_for(user_id):
        return None

    fields: dict[str, Any] = {}
    if item.get("counterparty"):
        fields["counterparty"] = str(item["counterparty"]).strip()
    if item.get("title"):
        fields["title"] = str(item["title"]).strip()
    if item.get("amount") is not None:
        fields["amount"] = coerce_amount(item["amount"])
    currency = normalize_currency(item.get("currency"))
    if currency:
        fields["currency_display"] = currency
    if item.get("due_text"):
        fields["due_text"] = str(item["due_text"]).strip()
    if item.get("note"):
        fields["note"] = str(item["note"]).strip()
    if item.get("kind"):
        fields["kind"] = normalize_kind(item["kind"])

    settle_amount = coerce_amount(item.get("settle_amount"))
    if item.get("settled"):
        total = fields.get("amount", debt.get("amount"))
        if total is not None:
            fields["settled_amount"] = total
    elif settle_amount is not None:
        fields["settled_amount"] = (debt.get("settled_amount") or 0) + settle_amount

    if fields:
        repo.update_debt(debt_id, **fields)
    sync_status(debt_id)
    return debt_id


def settle(user_id: int, debt_id: int) -> Optional[int]:
    """دکمه‌ی «تسویه شد» — کل مانده را تسویه می‌کند."""
    return apply_update(user_id, debt_id, {"settled": True})


def reopen(user_id: int, debt_id: int) -> Optional[int]:
    debt = repo.get_debt(debt_id)
    if not debt or debt.get("household_id") != repo.household_id_for(user_id):
        return None
    repo.update_debt(debt_id, settled_amount=0, settled_at=None)
    sync_status(debt_id)
    return debt_id


def totals(user_id: int) -> dict[str, dict[str, float]]:
    """مانده‌ی بازِ بدهی‌ها و طلب‌های خانوار، به تفکیکِ واحد پول."""
    out: dict[str, dict[str, float]] = {DEBT: {}, CREDIT: {}}
    for debt in repo.list_debts(user_id):
        rest = remaining(debt)
        if not rest:
            continue
        bucket = out[normalize_kind(debt["kind"])]
        currency = debt.get("currency_display") or "toman"
        bucket[currency] = bucket.get(currency, 0) + rest
    return out


def net_toman(user_id: int) -> int:
    """خالصِ تومانیِ (طلب − بدهی). ارزهای خارجی در این عدد نمی‌آیند."""
    balance = 0
    for debt in repo.list_debts(user_id):
        rest = remaining(debt)
        if not rest:
            continue
        rial = to_rial(rest, debt.get("currency_display", "toman"))
        balance += rial if normalize_kind(debt["kind"]) == CREDIT else -rial
    return int(balance // 10)
