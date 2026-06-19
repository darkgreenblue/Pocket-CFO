"""هسته‌ی مکالمه:
- فاز ۱: استخراج ساختاریافته از متن و/یا چند ویس در **یک** درخواست
  (پاسخ + transcript + تراکنش‌های جدید + ویرایش‌ها + needs_data)
- فاز ۲ (فقط اگر سؤال دیتایی بود): پاسخ با ابزارهای محاسباتی/خواندنی
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from bot.config import settings
from bot.llm.client import chat
from bot.llm.prompts import EXTRACT_SYSTEM, PROFILE_BLOCK, QUERY_SYSTEM
from bot.llm.tools import TOOLS_SPEC, dispatch
from bot.services import transactions as txn_service

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    reply: str
    transcript: str = ""
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)


def _audio_part(ogg_bytes: bytes) -> dict:
    b64 = base64.b64encode(ogg_bytes).decode("ascii")
    return {"type": "input_audio", "input_audio": {"data": b64, "format": "ogg"}}


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
    return {"reply": "", "transcript": "", "transactions": [], "updates": [], "needs_data": False}


async def _run_extraction(
    *,
    user_content: Union[str, list],
    has_audio: bool,
    user_id: int,
    history: Optional[list[dict]],
    profile: str,
    allowed_tags: list[str],
) -> AgentResult:
    system = EXTRACT_SYSTEM.format(
        tags="، ".join(allowed_tags), default_currency=settings.default_currency,
    )
    if profile.strip():
        system += PROFILE_BLOCK.format(profile=profile.strip())

    messages = [{"role": "system", "content": system}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_content})

    # با ورودی صوتی، response_format=json را به کار نمی‌بریم و با پرامپت + parse نرم پیش می‌رویم.
    raw = await chat(messages, json_mode=not has_audio)
    data = _loads_lenient(raw.content or "")

    created: list[int] = []
    for item in data.get("transactions") or []:
        try:
            created.append(txn_service.create_from_item(
                user_id, item, transcript=data.get("transcript", ""), source="chat"))
        except Exception:  # noqa: BLE001
            logger.exception("ساخت تراکنش ناموفق بود")

    updated: list[int] = []
    for upd in data.get("updates") or []:
        tid = upd.get("transaction_id")
        if tid is not None:
            res = txn_service.apply_update(user_id, int(tid), upd)
            if res is not None:
                updated.append(res)

    reply = (data.get("reply") or "").strip()
    transcript = (data.get("transcript") or "").strip()

    if data.get("needs_data"):
        answer = await _answer_data(transcript or (user_content if isinstance(user_content, str) else ""),
                                    history, user_id)
        if answer:
            reply = f"{reply}\n\n{answer}".strip() if reply else answer

    return AgentResult(reply=reply or "باشه 🙂", transcript=transcript,
                       created=created, updated=updated)


async def converse(*, user_text: str, user_id: int, history=None, profile: str = "",
                   allowed_tags=None, context_note: str = "") -> AgentResult:
    """ورودی متنی."""
    content = f"{context_note}\n\n{user_text}" if context_note else user_text
    return await _run_extraction(user_content=content, has_audio=False, user_id=user_id,
                                 history=history, profile=profile, allowed_tags=allowed_tags or [])


async def converse_audio(*, audio_ogg: bytes, user_id: int, history=None, profile: str = "",
                         allowed_tags=None, context_note: str = "") -> AgentResult:
    """ورودی یک ویس — رونویسی و استخراج در یک کال."""
    instruction = "این پیام صوتی فارسی را رونویسی کن و طبق قوانین، خرج‌ها را استخراج کن."
    if context_note:
        instruction = f"{context_note}\n{instruction}"
    content = [{"type": "text", "text": instruction}, _audio_part(audio_ogg)]
    return await _run_extraction(user_content=content, has_audio=True, user_id=user_id,
                                 history=history, profile=profile, allowed_tags=allowed_tags or [])


async def converse_batch(*, text_parts: list[str], audio_blobs: list[bytes], user_id: int,
                         history=None, profile: str = "", allowed_tags=None) -> AgentResult:
    """چند متن و/یا چند ویس در یک درخواست (صفِ بعد-از-ساعت‌کاری)."""
    intro = ("این‌ها خرج‌هایی است که کاربر بعد از پایان سهمیه‌ی قبلی پشت‌سرهم گفته. "
             "همه را با هم و کامل ثبت کن (هر کدام یک یا چند تراکنش).")
    content: list[dict] = [{"type": "text", "text": intro}]
    for t in text_parts:
        if t and t.strip():
            content.append({"type": "text", "text": t.strip()})
    for blob in audio_blobs:
        content.append(_audio_part(blob))
    has_audio = bool(audio_blobs)
    return await _run_extraction(user_content=content, has_audio=has_audio, user_id=user_id,
                                 history=history, profile=profile, allowed_tags=allowed_tags or [])


async def _answer_data(user_text: str, history: Optional[list[dict]], user_id: int) -> str:
    messages = [{"role": "system", "content": QUERY_SYSTEM}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_text or "گزارش بده."})

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
