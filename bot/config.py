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
    llm_timeout: int
    llm_retry_delay: int
    allowed_user_ids: frozenset[int]
    default_currency: str
    reminder_hour: int
    db_path: str

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
LLM_TIMEOUT_SECONDS = 15
LLM_RETRY_DELAY_SECONDS = 5
DEFAULT_CURRENCY = "toman"          # toman | rial
REMINDER_HOUR = 22                  # ساعت یادآوری شبانه (به وقت تهران)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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
        llm_timeout=int(_get("LLM_TIMEOUT", str(LLM_TIMEOUT_SECONDS))),
        llm_retry_delay=int(_get("LLM_RETRY_DELAY", str(LLM_RETRY_DELAY_SECONDS))),
        allowed_user_ids=allowed,
        default_currency=_get("DEFAULT_CURRENCY", DEFAULT_CURRENCY),
        reminder_hour=int(_get("REMINDER_HOUR", str(REMINDER_HOUR))),
        db_path=_get("DB_PATH", "data/pocket_cfo.db"),
    )


settings = load_settings()
