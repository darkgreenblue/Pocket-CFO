"""بارگذاری و اعتبارسنجی تنظیمات از فایل .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(key, default)
    if required and not value:
        raise RuntimeError(
            f"متغیر محیطی الزامی '{key}' تنظیم نشده است. "
            f"یک فایل .env بساز (از روی .env.example) و آن را پر کن."
        )
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openrouter_api_key: str
    openrouter_base_url: str
    llm_primary_model: str
    llm_fallback_model: str
    llm_audio_fallback_model: str
    llm_timeout: int
    llm_retry_delay: int
    llm_max_output_tokens: int
    allowed_user_ids: frozenset[int]
    default_currency: str
    reminder_hour: int
    db_path: str
    max_voice_seconds: int
    chat_history_turns: int
    rate_limit_max: int
    rate_limit_window: int
    daily_llm_limit: int
    history_max_messages: int
    max_tool_rounds: int

    def is_authorized(self, user_id: int) -> bool:
        # اگر لیست خالی باشد یعنی محدودیتی نگذاشته‌ایم (مناسب اولین راه‌اندازی).
        return not self.allowed_user_ids or user_id in self.allowed_user_ids


# ─────────────────────────────────────────────────────────────────────────
# تنظیمات قابل‌تغییر در حین توسعه/تست MVP.
# برای عوض کردن مدل یا رفتار، همین مقادیر را اینجا تغییر بده و push کن
# (روی سرور دستکاری لازم نیست). فقط کلیدهای محرمانه از .env خوانده می‌شوند.
# ─────────────────────────────────────────────────────────────────────────
PRIMARY_MODEL = "google/gemini-2.5-flash"
FALLBACK_MODEL = "google/gemini-2.5-flash-lite"
AUDIO_FALLBACK_MODEL = "openai/gpt-4o-mini-audio-preview"  # فقط mp3/wav → نیاز به ffmpeg
LLM_TIMEOUT_SECONDS = 15
LLM_RETRY_DELAY_SECONDS = 5
LLM_MAX_OUTPUT_TOKENS = 1200        # سقف خروجی برای کنترل هزینه
DEFAULT_CURRENCY = "toman"          # toman | rial
REMINDER_HOUR = 22                  # ساعت یادآوری/آپدیت پروفایل شبانه (به وقت تهران)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_VOICE_SECONDS = 180            # سقف طول ویس (ضدّ سوزاندن توکن)
CHAT_HISTORY_TURNS = 8              # تعداد پیام اخیرِ نگه‌داشته‌شده برای مکالمه
RATE_LIMIT_MAX = 8                  # سقف ضدّ-اسپمِ پیام در پنجره‌ی کوتاه، per-user
RATE_LIMIT_WINDOW = 30             # طول پنجره به ثانیه
DAILY_LLM_LIMIT = 20               # سقف پیام‌های روزانه که به LLM می‌روند (مدیریت هزینه)
HISTORY_MAX_MESSAGES = 50          # سقف پیام‌های حافظه‌ی همان روز که به مدل داده می‌شود
MAX_TOOL_ROUNDS = 12               # سقف دور‌های tool-calling در هر پیام (چند هزینه در یک پیام)


def load_settings() -> Settings:
    raw_ids = _get("ALLOWED_USER_IDS", "") or ""
    allowed = frozenset(
        int(x) for x in raw_ids.replace(" ", "").split(",") if x.strip()
    )
    # مقادیر بالا پیش‌فرض‌اند؛ در صورت نیاز می‌توان با متغیر محیطی هم‌نام override کرد،
    # ولی برای ربات شخصی لازم نیست و تغییر در کد کافی است.
    return Settings(
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN", required=True),
        openrouter_api_key=_get("OPENROUTER_API_KEY", required=True),
        openrouter_base_url=_get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
        llm_primary_model=_get("LLM_PRIMARY_MODEL", PRIMARY_MODEL),
        llm_fallback_model=_get("LLM_FALLBACK_MODEL", FALLBACK_MODEL),
        llm_audio_fallback_model=_get("LLM_AUDIO_FALLBACK_MODEL", AUDIO_FALLBACK_MODEL),
        llm_timeout=int(_get("LLM_TIMEOUT", str(LLM_TIMEOUT_SECONDS))),
        llm_retry_delay=int(_get("LLM_RETRY_DELAY", str(LLM_RETRY_DELAY_SECONDS))),
        llm_max_output_tokens=int(_get("LLM_MAX_OUTPUT_TOKENS", str(LLM_MAX_OUTPUT_TOKENS))),
        allowed_user_ids=allowed,
        default_currency=_get("DEFAULT_CURRENCY", DEFAULT_CURRENCY),
        reminder_hour=int(_get("REMINDER_HOUR", str(REMINDER_HOUR))),
        db_path=_get("DB_PATH", "data/pocket_cfo.db"),
        max_voice_seconds=int(_get("MAX_VOICE_SECONDS", str(MAX_VOICE_SECONDS))),
        chat_history_turns=int(_get("CHAT_HISTORY_TURNS", str(CHAT_HISTORY_TURNS))),
        rate_limit_max=int(_get("RATE_LIMIT_MAX", str(RATE_LIMIT_MAX))),
        rate_limit_window=int(_get("RATE_LIMIT_WINDOW", str(RATE_LIMIT_WINDOW))),
        daily_llm_limit=int(_get("DAILY_LLM_LIMIT", str(DAILY_LLM_LIMIT))),
        history_max_messages=int(_get("HISTORY_MAX_MESSAGES", str(HISTORY_MAX_MESSAGES))),
        max_tool_rounds=int(_get("MAX_TOOL_ROUNDS", str(MAX_TOOL_ROUNDS))),
    )


settings = load_settings()
