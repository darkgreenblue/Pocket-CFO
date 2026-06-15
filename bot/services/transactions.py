"""منطق کسب‌وکار تراکنش‌ها: ساخت draft از خروجی LLM، تطبیق تگ‌ها."""
from __future__ import annotations

from typing import Any

from bot.config import settings
from bot.db import repo
from bot.services import tags as tags_service


def create_draft_from_item(user_id: int, item: dict[str, Any], *, transcript: str, source: str) -> int:
    """یک تراکنش draft از یک آیتم استخراج‌شده‌ی LLM می‌سازد و تگ‌ها را تطبیق می‌دهد."""
    txn_id = repo.create_transaction(
        user_id=user_id,
        title=item["title"] or None,
        amount=item["total_amount"],
        currency_display=settings.default_currency,
        note=item.get("note", ""),
        mentioned_items=item.get("mentioned_items", []),
        needs_later_completion=item.get("needs_later_completion", False),
        transcript=transcript,
        source=source,
    )

    tag_bank = repo.get_tags()
    matched_ids, _, unmatched = tags_service.reconcile(item.get("suggested_tags", []), tag_bank)
    for tag_id in matched_ids:
        repo.add_transaction_tag(txn_id, tag_id)
    for name in unmatched:
        repo.add_tag_suggestion(user_id, txn_id, name)

    return txn_id


def is_complete(txn: dict[str, Any]) -> bool:
    """آیا فیلدهای اجباری (مبلغ و عنوان) پر شده‌اند؟"""
    return bool(txn.get("title")) and txn.get("amount") is not None
