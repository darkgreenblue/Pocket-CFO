"""روترِ نیت: یک کالِ کوچک و ارزان که پیش از هر پردازشی نیتِ پیام را می‌خواند.

خروجی سه چیز است:
  • record     — پیام باید وارد خط لوله‌ی ثبت/ویرایش/هدف شود.
  • data_query — کاربر درباره‌ی دیتای ثبت‌شده‌ی خودش می‌پرسد (مسیرِ ابزارِ خواندنی).
  • reply      — پاسخِ گفتگویی (سلام/قابلیت/مشاوره/بی‌ربط) که همین‌جا داده می‌شود.

اگر پیام فقط گفتگویی باشد، هیچ کالِ دوم و هیچ کانتکستِ سنگینی لازم نیست؛ همین یک کال کافی
است. فقط برای ثبت (استخراج) و سؤالِ دیتایی (ابزارها) یک کالِ تخصصیِ دوم اجرا می‌شود.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from bot.llm.client import chat
from bot.llm.prompts import CAPABILITIES, ROUTER_SYSTEM
from bot.utils import jalali

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    record: bool = False
    data_query: bool = False
    reply: str = ""


def parse_decision(raw: str) -> Decision:
    """خروجیِ خامِ روتر را به Decision تبدیل می‌کند (تحملِ ```/متنِ اضافه)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        logger.error("نتوانستم خروجی روتر را parse کنم: %s", (raw or "")[:200])
        # در صورتِ ابهام، محافظه‌کارانه: پیام را گفتگویی فرض کن تا «باشه»ِ خالی ندهیم.
        return Decision(record=False, data_query=False, reply=(raw or "").strip())
    return Decision(
        record=bool(data.get("record")),
        data_query=bool(data.get("data_query")),
        reply=(data.get("reply") or "").strip(),
    )


def _history_messages(history: Optional[list[dict]]) -> list[dict]:
    out = []
    for m in history or []:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            out.append({"role": m["role"], "content": m["content"]})
    return out


async def route(*, text: str, history: Optional[list[dict]] = None,
                reply_note: str = "") -> Decision:
    """نیتِ یک پیامِ متنی (یا رونویسیِ ویس) را تعیین می‌کند."""
    system = ROUTER_SYSTEM.format(today=jalali.today_str(), capabilities=CAPABILITIES)
    user_content = f"{reply_note}\n\n{text}" if reply_note else text
    messages = [{"role": "system", "content": system}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_content})
    raw = await chat(messages, json_mode=True)
    return parse_decision(raw.content or "")
