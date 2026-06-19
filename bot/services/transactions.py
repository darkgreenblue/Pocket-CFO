"""منطق تراکنش‌ها: ساخت از آیتم استخراج‌شده، ویرایش، و کمک‌تابع‌ها."""
from __future__ import annotations

from typing import Any, Optional

from bot.config import settings
from bot.db import repo
from bot.services import tags as tags_service

KNOWN_CURRENCIES = {"toman", "rial", "usd", "eur", "usdt", "btc", "aed", "try"}


def is_complete(txn: dict[str, Any]) -> bool:
    """فیلدهای اجباری (مبلغ و عنوان) هر دو پر شده‌اند؟"""
    return txn.get("amount") is not None and bool((txn.get("title") or "").strip())


def normalize_currency(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.lower() in KNOWN_CURRENCIES:
        return value.lower()
    return None


def coerce_amount(value: Any):
    """عدد را به int تبدیل می‌کند مگر اعشاری واقعی باشد (ارز خارجی)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _apply_tags(txn_id: int, suggested: list[str], user_id: int) -> None:
    tag_bank = repo.get_tags()
    matched_ids, _, unmatched = tags_service.reconcile(suggested or [], tag_bank)
    for tid in matched_ids:
        repo.add_transaction_tag(txn_id, tid)
    for nm in unmatched:
        repo.add_tag_suggestion(user_id, txn_id, nm)


def create_from_item(user_id: int, item: dict[str, Any], *, transcript: str = "",
                     source: str = "chat") -> int:
    """یک تراکنش از آیتم استخراج‌شده‌ی LLM می‌سازد (کامل → confirmed، ناقص → draft)."""
    title = (item.get("title") or "").strip() or None
    if title is None and item.get("title_unimportant"):
        title = "بدون عنوان"
    amount = coerce_amount(item.get("amount"))
    currency = normalize_currency(item.get("currency")) or settings.default_currency
    complete = amount is not None and bool(title)
    status = "confirmed" if complete else "draft"

    txn_id = repo.create_transaction(
        user_id=user_id, title=title, amount=amount, currency_display=currency,
        note=(item.get("note") or "").strip(),
        mentioned_items=[str(x) for x in (item.get("mentioned_items") or [])],
        needs_later_completion=bool(item.get("needs_later_completion", False)),
        transcript=transcript, source=source, status=status,
    )
    _apply_tags(txn_id, item.get("suggested_tags") or [], user_id)
    return txn_id


def apply_update(user_id: int, txn_id: int, item: dict[str, Any]) -> Optional[int]:
    """ویرایش یک تراکنش موجود. در صورت موفقیت id را برمی‌گرداند."""
    txn = repo.get_transaction(txn_id)
    if not txn or txn.get("user_id") != user_id:
        return None
    fields: dict[str, Any] = {}
    if item.get("title"):
        fields["title"] = str(item["title"]).strip()
    if item.get("amount") is not None:
        fields["amount"] = coerce_amount(item["amount"])
    cur = normalize_currency(item.get("currency"))
    if cur:
        fields["currency_display"] = cur
    if item.get("note"):
        fields["note"] = str(item["note"]).strip()
    if fields:
        repo.update_transaction(txn_id, **fields)
    repo.sync_status(txn_id)
    return txn_id
