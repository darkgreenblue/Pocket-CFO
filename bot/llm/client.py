"""کلاینت LLM روی OpenRouter با زنجیره‌ی Retry/Fallback.

ترتیب تلاش‌ها برای ورودی صوتی:
  ۱) gemini-2.5-flash (ogg)            — تایم‌اوت سختگیرانه
  ۲) ۵ ثانیه صبر، دوباره gemini-2.5-flash (ogg)
  ۳) gpt-4o-mini-audio (mp3؛ تبدیل با ffmpeg، فقط همین‌جا)
  ۴) gemini-2.5-flash-lite (ogg)
  ۵) خطا به کاربر
برای ورودی متنی همان زنجیره بدون مرحله‌ی صوتی.
هر مرحله یک callable سازنده‌ی messages دارد که فقط لحظه‌ی اجرا ساخته می‌شود
(تا تبدیل ffmpeg بی‌خود انجام نشود).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from bot.config import settings

logger = logging.getLogger(__name__)

USER_FACING_UNAVAILABLE = (
    "متأسفانه در حال حاضر سرویس‌های هوش مصنوعی پاسخگو نیستند. "
    "لطفاً چند دقیقه دیگر دوباره تلاش کن."
)


class LLMUnavailableError(Exception):
    """وقتی تمام مدل‌های زنجیره شکست خوردند."""


@dataclass
class Attempt:
    model: str
    build_messages: Callable[[], Awaitable[list[dict]]]
    delay_before: float = 0.0


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    return _client


async def _create(model: str, messages: list[dict], json_mode: bool) -> str | None:
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": settings.llm_max_output_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await _get_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content


async def run_chain(attempts: list[Attempt], *, json_mode: bool = True) -> str:
    """زنجیره را به ترتیب اجرا می‌کند؛ اولین پاسخ موفق را برمی‌گرداند."""
    last_error: Exception | None = None
    for attempt in attempts:
        if attempt.delay_before:
            await asyncio.sleep(attempt.delay_before)
        try:
            messages = await attempt.build_messages()
            content = await asyncio.wait_for(
                _create(attempt.model, messages, json_mode),
                timeout=settings.llm_timeout,
            )
            if content and content.strip():
                logger.info("LLM پاسخ داد (model=%s)", attempt.model)
                return content
            last_error = ValueError("پاسخ خالی از مدل")
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("تلاش LLM ناموفق (model=%s): %s", attempt.model, exc)
    raise LLMUnavailableError(str(last_error))


def _chat_attempts() -> list[tuple[str, float]]:
    """(model, delay_before) — فلش، دوباره فلش بعد از وقفه، سپس فلش‌لایت."""
    return [
        (settings.llm_primary_model, 0.0),
        (settings.llm_primary_model, float(settings.llm_retry_delay)),
        (settings.llm_fallback_model, 0.0),
    ]


async def chat(messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False):
    """یک درخواست chat را با زنجیره‌ی فال‌بک می‌زند و پیام دستیار را برمی‌گرداند."""
    client = _get_client()
    last_error: Exception | None = None
    for model, delay in _chat_attempts():
        if delay:
            await asyncio.sleep(delay)
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": settings.llm_max_output_tokens,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await asyncio.wait_for(
                client.chat.completions.create(**kwargs), timeout=settings.llm_timeout
            )
            return resp.choices[0].message
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("تلاش chat ناموفق (model=%s): %s", model, exc)
    raise LLMUnavailableError(str(last_error))


async def complete_text(model: str, messages: list[dict], timeout: int = 45) -> str:
    """یک کال متنی ساده (برای کارهای پس‌زمینه مثل آپدیت پروفایل)."""
    resp = await asyncio.wait_for(
        _get_client().chat.completions.create(
            model=model, messages=messages, temperature=0.3, max_tokens=800,
        ),
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""
