"""هسته‌ی مکالمه: رونویسی صوت + حلقه‌ی tool-calling روی دیتای کاربر."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from bot.config import settings
from bot.llm.client import chat
from bot.llm.prompts import PROFILE_BLOCK, SYSTEM_PROMPT
from bot.llm.tools import TOOLS_SPEC, dispatch

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    reply: str
    transcript: str = ""
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)


async def transcribe(audio_ogg: bytes) -> str:
    """ویس فارسی را به متن تبدیل می‌کند (یک کال، بدون tool)."""
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


def _system_message(allowed_tags: list[str], profile: str) -> dict:
    content = SYSTEM_PROMPT.format(
        tags="، ".join(allowed_tags),
        default_currency=settings.default_currency,
    )
    if profile.strip():
        content += PROFILE_BLOCK.format(profile=profile.strip())
    return {"role": "system", "content": content}


def _serialize_tool_calls(tool_calls) -> list[dict]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in tool_calls
    ]


async def converse(
    *,
    user_text: str,
    user_id: int,
    history: Optional[list[dict]] = None,
    profile: str = "",
    allowed_tags: Optional[list[str]] = None,
    context_note: str = "",
) -> AgentResult:
    """مکالمه + اجرای ابزارها. ورودی متن است (ویس از قبل رونویسی شده)."""
    allowed_tags = allowed_tags or []
    messages: list[dict] = [_system_message(allowed_tags, profile)]
    for m in history or []:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})

    user_content = user_text
    if context_note:
        user_content = f"{context_note}\n\n{user_text}"
    messages.append({"role": "user", "content": user_content})

    effects: dict[str, list] = {"created": [], "updated": []}
    reply = ""

    for _ in range(settings.max_tool_rounds):
        msg = await chat(messages, tools=TOOLS_SPEC)
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            reply = (msg.content or "").strip()
            break
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": _serialize_tool_calls(tool_calls),
        })
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(tc.function.name, args, user_id=user_id, effects=effects)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        # سقف دور‌ها پر شد؛ یک پاسخ نهایی بدون ابزار بگیر
        try:
            msg = await chat(messages)
            reply = (msg.content or "").strip()
        except Exception:  # noqa: BLE001
            reply = ""

    return AgentResult(reply=reply, created=effects["created"], updated=effects["updated"])
