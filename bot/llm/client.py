"""کلاینت LLM روی OpenRouter با زنجیره‌ی Retry/Fallback.

ترتیب تلاش‌ها (هر کدام با تایم‌اوت سختگیرانه):
  ۱) مدل اصلی (gemini-2.5-flash)
  ۲) همان مدل، پس از وقفه‌ی کوتاه
  ۳) مدل فال‌بک (gemini-2.5-flash-lite)
اگر همه شکست بخورند، ``LLMUnavailableError`` پرتاب می‌شود.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI

from bot.config import settings

logger = logging.getLogger(__name__)

USER_FACING_UNAVAILABLE = (
    "متأسفانه در حال حاضر سرویس‌های هوش مصنوعی پاسخگو نیستند. "
    "لطفاً چند دقیقه دیگر دوباره تلاش کن."
)


class LLMUnavailableError(Exception):
    """وقتی تمام مدل‌های زنجیره شکست خوردند."""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    return _client


def _attempts() -> list[tuple[str, int]]:
    """(model, delay_before_seconds)"""
    return [
        (settings.llm_primary_model, 0),
        (settings.llm_primary_model, settings.llm_retry_delay),
        (settings.llm_fallback_model, 0),
    ]


async def complete_json(messages: list[dict[str, Any]]) -> str:
    """درخواست chat completion با خروجی JSON و زنجیره‌ی fallback."""
    client = _get_client()
    last_error: Exception | None = None

    for model, delay in _attempts():
        if delay:
            await asyncio.sleep(delay)
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.2,
                ),
                timeout=settings.llm_timeout,
            )
            content = resp.choices[0].message.content
            if content and content.strip():
                logger.info("LLM پاسخ داد (model=%s)", model)
                return content
            last_error = ValueError("پاسخ خالی از مدل")
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("تلاش LLM ناموفق (model=%s): %s", model, exc)

    raise LLMUnavailableError(str(last_error))
