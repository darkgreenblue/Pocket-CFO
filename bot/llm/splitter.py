"""افراز متن بلند به بخش‌های معنادار (تا هیچ تراکنشی نصفه نشود).

اول یک کالِ flash می‌زند؛ اگر خروجی معتبر نبود، به برشِ قطعیِ مرزِ خط/جمله برمی‌گردد.
تصمیمِ «چند پارت» قطعی و بر اساس طول کاراکتری است.
"""
from __future__ import annotations

import json
import logging
import re

from bot.config import settings
from bot.llm.client import chat
from bot.llm.prompts import SPLIT_SYSTEM

logger = logging.getLogger(__name__)


def decide_parts(text: str) -> int:
    """تعداد پارت‌ها بر اساس طول؛ -1 یعنی طولانی‌تر از حد مجاز."""
    n = len((text or "").strip())
    if n <= settings.chunk_2_parts:
        return 1
    if n <= settings.chunk_3_parts:
        return 2
    if n <= settings.chunk_max_chars:
        return settings.max_parts
    return -1


def _norm_len(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


def _naive_split(text: str, n: int) -> list[str]:
    """برشِ قطعی روی مرزِ خط/جمله به n بخشِ تقریباً هم‌اندازه."""
    units = [u for u in re.split(r"(?<=[\n۰-۹0-9])\s*\n|(?<=[.!؟])\s+|\n", text) if u.strip()]
    if len(units) < n:
        units = [u for u in re.split(r"[،,]\s*|\n", text) if u.strip()]
    if len(units) <= n:
        return [text.strip()] if len(units) <= 1 else [u.strip() for u in units]
    target = _norm_len(text) / n
    parts, cur, cur_len = [], [], 0
    for u in units:
        cur.append(u)
        cur_len += _norm_len(u)
        if len(parts) < n - 1 and cur_len >= target:
            parts.append(" ".join(cur).strip())
            cur, cur_len = [], 0
    if cur:
        parts.append(" ".join(cur).strip())
    return [p for p in parts if p]


async def split_text(text: str, n: int) -> list[str]:
    if n <= 1:
        return [text]
    try:
        messages = [
            {"role": "system", "content": SPLIT_SYSTEM.format(n=n)},
            {"role": "user", "content": text},
        ]
        raw = await chat(messages, json_mode=True)
        data = json.loads((raw.content or "").strip().strip("`"))
        parts = [str(p).strip() for p in (data.get("parts") or []) if str(p).strip()]
        # اعتبارسنجی: تعداد درست و پوششِ کافیِ متن اصلی
        if len(parts) == n and _norm_len(" ".join(parts)) >= 0.8 * _norm_len(text):
            return parts
        logger.warning("افرازِ LLM نامعتبر بود؛ fallback قطعی.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("افرازِ LLM ناموفق (%s)؛ fallback قطعی.", exc)
    return _naive_split(text, n)
