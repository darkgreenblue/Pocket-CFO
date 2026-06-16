"""مکالمه + استخراج تراکنش از ویس/متن در یک کال، با زنجیره‌ی فال‌بک."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from bot.config import settings
from bot.llm.client import Attempt, run_chain
from bot.llm.prompts import (
    PROFILE_BLOCK,
    SYSTEM_PROMPT,
    build_user_instruction,
)

logger = logging.getLogger(__name__)

KNOWN_CURRENCIES = {"toman", "rial", "usd", "eur", "usdt", "btc", "aed", "try"}


def _system_message(allowed_tags: list[str], profile: str) -> dict:
    content = SYSTEM_PROMPT.format(
        tags="، ".join(allowed_tags),
        default_currency=settings.default_currency,
    )
    if profile.strip():
        content += PROFILE_BLOCK.format(profile=profile.strip())
    return {"role": "system", "content": content}


def _history_messages(history: list[dict]) -> list[dict]:
    out = []
    for m in history or []:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            out.append({"role": role, "content": m["content"]})
    return out


async def converse_and_extract(
    *,
    text: Optional[str] = None,
    audio_ogg: Optional[bytes] = None,
    history: Optional[list[dict]] = None,
    profile: str = "",
    allowed_tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """خروجی: dict با کلیدهای ``reply``، ``transcript`` و ``transactions``."""
    allowed_tags = allowed_tags or []
    base = [_system_message(allowed_tags, profile)] + _history_messages(history)
    instruction = build_user_instruction(has_audio=audio_ogg is not None)

    if audio_ogg is not None:
        async def build_gemini() -> list[dict]:
            b64 = base64.b64encode(audio_ogg).decode("ascii")
            return base + [{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "input_audio", "input_audio": {"data": b64, "format": "ogg"}},
                ],
            }]

        attempts = [
            Attempt(settings.llm_primary_model, build_gemini),
            Attempt(settings.llm_primary_model, build_gemini, delay_before=settings.llm_retry_delay),
            Attempt(settings.llm_fallback_model, build_gemini),
        ]
    else:
        async def build_text() -> list[dict]:
            return base + [{"role": "user", "content": f"{instruction}\n\nپیام کاربر:\n{text}"}]

        attempts = [
            Attempt(settings.llm_primary_model, build_text),
            Attempt(settings.llm_primary_model, build_text, delay_before=settings.llm_retry_delay),
            Attempt(settings.llm_fallback_model, build_text),
        ]

    raw = await run_chain(attempts)
    return _parse(raw, fallback_text=text or "")


def _parse(raw: str, fallback_text: str) -> dict[str, Any]:
    data = _loads_lenient(raw)
    transactions = []
    for item in data.get("transactions") or []:
        amount = item.get("total_amount")
        try:
            amount = int(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        currency = item.get("currency")
        currency = currency.lower() if isinstance(currency, str) and currency.lower() in KNOWN_CURRENCIES else None
        transactions.append({
            "title": (item.get("title") or "").strip(),
            "total_amount": amount,
            "currency": currency,  # None → سیستم واحد پیش‌فرض را می‌گذارد
            "mentioned_items": [str(x) for x in (item.get("mentioned_items") or [])],
            "suggested_tags": [str(x) for x in (item.get("suggested_tags") or [])],
            "needs_later_completion": bool(item.get("needs_later_completion", False)),
            "note": (item.get("note") or "").strip(),
        })
    return {
        "reply": (data.get("reply") or "").strip(),
        "transcript": (data.get("transcript") or fallback_text).strip(),
        "transactions": transactions,
    }


def _loads_lenient(raw: str) -> dict[str, Any]:
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
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.error("نتوانستم خروجی LLM را parse کنم: %s", raw[:300])
        return {"reply": "", "transcript": "", "transactions": []}
