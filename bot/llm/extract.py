"""استخراج تراکنش‌ها از ویس/متن در یک درخواست واحد به LLM."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from bot.llm.client import complete_json
from bot.llm.prompts import SYSTEM_PROMPT, build_user_instruction

logger = logging.getLogger(__name__)


async def extract(
    *,
    text: Optional[str] = None,
    audio: Optional[bytes] = None,
    audio_format: str = "ogg",
    allowed_tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """ورودی صوت یا متن → دیکشنری با کلیدهای ``transcript`` و ``transactions``."""
    allowed_tags = allowed_tags or []
    system = SYSTEM_PROMPT.format(tags="، ".join(allowed_tags))

    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": build_user_instruction(has_audio=audio is not None)}
    ]
    if audio is not None:
        b64 = base64.b64encode(audio).decode("ascii")
        user_content.append(
            {"type": "input_audio", "input_audio": {"data": b64, "format": audio_format}}
        )
    elif text:
        user_content.append({"type": "text", "text": f"\nپیام کاربر:\n{text}"})

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    raw = await complete_json(messages)
    return _parse(raw)


def _parse(raw: str) -> dict[str, Any]:
    data = _loads_lenient(raw)
    transactions = []
    for item in data.get("transactions") or []:
        amount = item.get("total_amount")
        try:
            amount = int(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        transactions.append(
            {
                "title": (item.get("title") or "").strip(),
                "total_amount": amount,
                "mentioned_items": [str(x) for x in (item.get("mentioned_items") or [])],
                "suggested_tags": [str(x) for x in (item.get("suggested_tags") or [])],
                "needs_later_completion": bool(item.get("needs_later_completion", False)),
                "note": (item.get("note") or "").strip(),
            }
        )
    return {"transcript": (data.get("transcript") or "").strip(), "transactions": transactions}


def _loads_lenient(raw: str) -> dict[str, Any]:
    """JSON را با تحمل نسبت به code-fence و متن اضافه parse می‌کند."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        logger.error("نتوانستم خروجی LLM را parse کنم: %s", raw[:300])
        return {"transcript": "", "transactions": []}
