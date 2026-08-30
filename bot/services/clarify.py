"""ابهامِ «تراکنش یا بدهی؟» — به‌جای حدسِ اشتباه، از کاربر می‌پرسیم.

مرزِ بین «خرجی که انجام شد» و «بدهی‌ای که ثبت می‌شود» گاهی در خودِ جمله روشن نیست
(«پولِ رضا رو دادم» می‌تواند هر دو باشد). حدسِ اشتباه هزینه دارد: کاربر باید رکوردِ
غلط را پیدا و پاک کند. پس در آن حالت هیچ‌چیز ثبت نمی‌شود؛ مورد اینجا می‌ماند تا کاربر
با یک دکمه تعیین تکلیفش کند.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from bot.db import repo
from bot.services import debts as debts_service
from bot.services import transactions as txn_service
from bot.utils.money import format_amount

logger = logging.getLogger(__name__)

CHOICE_TXN = "txn"
CHOICE_DEBT = "debt"

ALREADY_ANSWERED = "این مورد قبلاً تعیین تکلیف شده."
EXPIRED = "این مورد دیگر در دسترس نیست."


def record(user_id: int, item: dict[str, Any]) -> Optional[int]:
    """آیتمِ مبهم را نگه می‌دارد و شناسه‌اش را می‌دهد. آیتمِ بی‌محتوا نادیده گرفته می‌شود."""
    if not any((item.get("title"), item.get("amount") is not None, item.get("counterparty"))):
        return None
    return repo.create_clarification(user_id, item)


def question(clar_id: int) -> Optional[str]:
    """متنِ سؤال، با همان چیزی که کاربر گفته تا بداند درباره‌ی کدام مورد است."""
    row = repo.get_clarification(clar_id)
    if not row:
        return None
    item = row["payload"]
    bits = []
    title = (item.get("title") or "").strip()
    if title:
        bits.append(title)
    if item.get("amount") is not None:
        bits.append(format_amount(item["amount"], item.get("currency") or "toman"))
    party = (item.get("counterparty") or "").strip()
    if party:
        bits.append(f"طرف حساب: {party}")
    subject = " · ".join(bits) or "این مورد"
    return (f"🤔 مطمئن نشدم این چیه:\n{subject}\n\n"
            "خرجی بود که انجام شد، یا بدهی/طلبی که باید ثبت بمونه؟")


def resolve(user_id: int, clar_id: int, choice: str) -> tuple[Optional[str], Optional[int]]:
    """انتخابِ کاربر را اعمال می‌کند. خروجی: (نوعِ ساخته‌شده، id) یا (None, None).

    نوعِ خروجی «txn» یا «debt» است تا هندلر بداند کدام کارت را بفرستد.
    """
    row = repo.get_clarification(clar_id)
    if not row or row.get("user_id") != user_id:
        return None, None
    if not repo.resolve_clarification(clar_id, choice):
        return None, None

    item = dict(row["payload"])
    if choice == CHOICE_DEBT:
        # مدل نیت را تشخیص نداده بود، پس جهت را هم تعیین نکرده؛ پیش‌فرضِ debt امن‌تر است
        # و کاربر با دکمه‌ی خودِ کارت می‌تواند عوضش کند.
        item.setdefault("kind", "debt")
        debt_id, _ = debts_service.create_or_update_from_item(user_id, item)
        return (CHOICE_DEBT, debt_id) if debt_id else (None, None)

    txn_id = txn_service.create_from_item(user_id, item, source="clarify")
    return CHOICE_TXN, txn_id
