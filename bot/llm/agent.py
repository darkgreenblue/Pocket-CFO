"""هسته‌ی مکالمه:
- رونویسی صوت
- فاز ۱: استخراج ساختاریافته (پاسخ + تراکنش‌های جدید + ویرایش‌ها) در یک کال JSON
- فاز ۲ (فقط اگر سؤال دیتایی بود): پاسخ با ابزارهای فقط-خواندنی
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from bot.config import settings
from bot.llm.client import chat
from bot.llm.prompts import EXTRACT_SYSTEM, PROFILE_BLOCK, QUERY_SYSTEM
from bot.llm.tools import TOOLS_SPEC, dispatch
from bot.services import transactions as txn_service

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    reply: str
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)


async def transcribe(audio_ogg: bytes) -> str:
    """ویس فارسی را به متن تبدیل می‌کند (یک کال)."""
    b64 = base64.b64encode(audio_ogg).decode("ascii")
    messages = [
        {"role": "system", "content": "تو فقط رونویس هستی. متن کامل گفته‌ی فارسی کاربر را بدون توضیح اضافه برگردان."},
        {"role": "user", "content": [
            {"type": "text", "text": "این پیام صوتی را دقیق رونویسی کن."},
            {"type": "input_audio", "input_audio": {"data": b64, "format": "ogg"}},
        ]},
    ]
    msg = await chat(messages)
    return (msg.content or "").strip()


def _history_messages(history: Optional[list[dict]]) -> list[dict]:
    out = []
    for m in history or []:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            out.append({"role": m["role"], "content": m["content"]})
    return out


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
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    logger.error("نتوانستم خروجی استخراج را parse کنم: %s", (raw or "")[:300])
    return {"reply": "", "transactions": [], "updates": [], "needs_data": False}


async def converse(
    *,
    user_text: str,
    user_id: int,
    history: Optional[list[dict]] = None,
    profile: str = "",
    allowed_tags: Optional[list[str]] = None,
    context_note: str = "",
) -> AgentResult:
    allowed_tags = allowed_tags or []
    system = EXTRACT_SYSTEM.format(
        tags="، ".join(allowed_tags), default_currency=settings.default_currency,
    )
    if profile.strip():
        system += PROFILE_BLOCK.format(profile=profile.strip())

    messages = [{"role": "system", "content": system}]
    messages += _history_messages(history)
    user_content = f"{context_note}\n\n{user_text}" if context_note else user_text
    messages.append({"role": "user", "content": user_content})

    # فاز ۱ — استخراج ساختاریافته
    raw = await chat(messages, json_mode=True)
    data = _loads_lenient(raw.content or "")

    created: list[int] = []
    for item in data.get("transactions") or []:
        try:
            created.append(txn_service.create_from_item(
                user_id, item, transcript=user_text, source="chat"))
        except Exception:  # noqa: BLE001
            logger.exception("ساخت تراکنش از آیتم استخراج‌شده ناموفق بود")

    updated: list[int] = []
    for upd in data.get("updates") or []:
        tid = upd.get("transaction_id")
        if tid is not None:
            res = txn_service.apply_update(user_id, int(tid), upd)
            if res is not None:
                updated.append(res)

    reply = (data.get("reply") or "").strip()

    # فاز ۲ — فقط اگر سؤال دیتایی بود
    if data.get("needs_data"):
        answer = await _answer_data(user_text, history, user_id)
        if answer:
            reply = f"{reply}\n\n{answer}".strip() if reply else answer

    return AgentResult(reply=reply or "باشه 🙂", created=created, updated=updated)


async def _answer_data(user_text: str, history: Optional[list[dict]], user_id: int) -> str:
    messages = [{"role": "system", "content": QUERY_SYSTEM}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_text})

    for _ in range(3):
        msg = await chat(messages, tools=TOOLS_SPEC)
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return (msg.content or "").strip()
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(tc.function.name, args, user_id=user_id)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return ""
