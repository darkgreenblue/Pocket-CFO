"""منطق مشترک تراکنش‌ها."""
from __future__ import annotations

from typing import Any


def is_complete(txn: dict[str, Any]) -> bool:
    """فیلدهای اجباری (مبلغ و عنوان) هر دو پر شده‌اند؟"""
    return txn.get("amount") is not None and bool((txn.get("title") or "").strip())
