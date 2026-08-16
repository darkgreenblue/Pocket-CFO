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
    chars_per_line: int
    chunk_2_parts: int
    chunk_3_parts: int
    chunk_max_chars: int
    max_parts: int
    voice_oneshot_max_seconds: int
    access_mode: str
    allowed_user_ids: frozenset[int]
    default_currency: str
    reminder_hour: int
    morning_hour: int
    db_path: str
    max_voice_seconds: int
    chat_history_turns: int
    rate_limit_max: int
    rate_limit_window: int
    daily_llm_limit: int
    history_max_messages: int
    max_tool_rounds: int
    ingest_host: str
    ingest_port: int
    ingest_tokens: dict[str, int]
    ingest_max_body_bytes: int
    ingest_audio_dir: str
    ingest_public_url: str

    @property
    def ingest_enabled(self) -> bool:
        """درِ دوم وقتی باز است که یا آدرسِ عمومی ست شده باشد یا توکنِ ثابتی وجود داشته باشد.

        `ingest_public_url` معیارِ اصلی است چون بدونِ آن `/shortcut` چیزی برای دادن به
        کاربر ندارد؛ `ingest_tokens` مسیرِ قدیمی/bootstrap را زنده نگه می‌دارد.
        """
        return bool(self.ingest_public_url or self.ingest_tokens)

    def is_authorized(self, user_id: int) -> bool:
        if self.access_mode == ACCESS_OPEN:
            return True
        # لیستِ خالی هم یعنی محدودیتی در عمل وجود ندارد (مناسب اولین راه‌اندازی).
        return not self.allowed_user_ids or user_id in self.allowed_user_ids

    def access_summary(self) -> str:
        """یک خط برای لاگِ راه‌اندازی — تا وضعیتِ واقعیِ سرور از بیرون معلوم باشد."""
        if self.access_mode == ACCESS_OPEN:
            return "دسترسی: باز برای همه"
        if not self.allowed_user_ids:
            return "دسترسی: allowlist ولی لیست خالی است → عملاً باز برای همه"
        ids = "، ".join(str(i) for i in sorted(self.allowed_user_ids))
        return f"دسترسی: محدود به {len(self.allowed_user_ids)} آی‌دی ({ids}) + اعضای خانوارشان"


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
LLM_MAX_OUTPUT_TOKENS = 4000        # سقف خروجی (لیست بلندِ تراکنش‌ها نباید بریده شود)
CHARS_PER_LINE = 60                 # مبنای «خط» برای تصمیم چانکینگ
CHUNK_2_PARTS = 600                 # >این → ۲ پارت
CHUNK_3_PARTS = 1200                # >این → ۳ پارت
CHUNK_MAX_CHARS = 1800              # >این → رد (طولانی‌تر از حد)
MAX_PARTS = 3
VOICE_ONESHOT_MAX_SECONDS = 60      # ویس کوتاه‌تر از این → تک‌کال؛ بلندتر → رونویسی‌+چانک
DEFAULT_CURRENCY = "toman"          # toman | rial
REMINDER_HOUR = 22                  # ساعت یادآوری/آپدیت پروفایل شبانه (به وقت تهران)
MORNING_HOUR = 10                   # ساعت پردازشِ صفِ پیام‌های بعد-از-ساعت‌کاری (به وقت تهران)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_VOICE_SECONDS = 120            # سقف طول ویس (حفاظِ هزینه/تأخیر؛ نه راه‌حلِ کیفیت)
CHAT_HISTORY_TURNS = 8              # تعداد پیام اخیرِ نگه‌داشته‌شده برای مکالمه
RATE_LIMIT_MAX = 8                  # سقف ضدّ-اسپمِ پیام در پنجره‌ی کوتاه، per-user
RATE_LIMIT_WINDOW = 30             # طول پنجره به ثانیه
DAILY_LLM_LIMIT = 20               # سقف پیام‌های روزانه که به LLM می‌روند (مدیریت هزینه)
HISTORY_MAX_MESSAGES = 50          # سقف پیام‌های حافظه‌ی همان روز که به مدل داده می‌شود
MAX_TOOL_ROUNDS = 12               # سقف دور‌های tool-calling در هر پیام (چند هزینه در یک پیام)

# ─── درِ دومِ ورودی (شرتکاتِ iOS) ───
# ربات علاوه بر تلگرام، یک endpoint کوچکِ HTTP هم دارد تا وقتی گوشیِ کاربر به تلگرام
# دسترسی ندارد (ولی به سرورِ ما دارد) بتواند خرجش را ثبت کند. کارتِ تأیید را سرور
# — که خودش به تلگرام دسترسی دارد — می‌فرستد و کاربر بعداً در تلگرام تأییدش می‌کند.
INGEST_HOST = "0.0.0.0"            # noqa: S104 — داخل کانتینر؛ انتشار با ports در compose
INGEST_PORT = 8791
INGEST_MAX_BODY_MB = 12            # سقف حجمِ بدنه (ویسِ base64 حدود ۱.۳۳ برابر می‌شود)
INGEST_AUDIO_DIR = "var/ingest_audio"   # ویسِ صف‌شده تا صبح اینجا می‌ماند
# آدرسی که گوشیِ کاربر می‌بیند (مثل http://1.2.3.4:8791). تا وقتی ست نشود، دستورِ
# /shortcut کار نمی‌کند — چون بدون آدرس، توکن به‌تنهایی به درد کاربر نمی‌خورد.
INGEST_PUBLIC_URL = ""


def _parse_ingest_tokens(raw: str) -> dict[str, int]:
    """`INGEST_TOKENS` را به نگاشتِ token → user_id تبدیل می‌کند.

    قالب: `<user_id>:<token>` جداشده با کاما. توکن کلیدِ نگاشت است تا جست‌وجو با
    خودِ توکنِ ورودی انجام شود و user_id هیچ‌وقت از سمتِ کلاینت نیاید.
    """
    tokens: dict[str, int] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        raw_id, _, token = chunk.partition(":")
        token = token.strip()
        try:
            user_id = int(raw_id.strip())
        except ValueError:
            continue
        # توکنِ کوتاه عملاً یعنی بی‌حفاظ؛ چون این کلیدِ نوشتن روی دفترِ مالی است.
        if len(token) >= 24:
            tokens[token] = user_id
    return tokens


# ─────────────────────────── دسترسی به ربات ───────────────────────────
# محدودیتِ فعلی **موقت** است: تا وقتی ربات در مرحله‌ی شخصی/بتاست فقط آی‌دی‌های زیر
# (به‌علاوه‌ی اعضای خانوارشان) اجازه‌ی استفاده دارند.
#
# 🔓 روزی که خواستی ربات را برای همه باز کنی: فقط همین یک خط را به
#    ACCESS_MODE = ACCESS_OPEN تغییر بده و push کن. نه سرور، نه .env، نه لیست —
#    از آن لحظه به هر کاربری جواب می‌دهد. برای برگرداندن، دوباره allowlist.
ACCESS_OPEN = "open"
ACCESS_ALLOWLIST = "allowlist"
ACCESS_MODE = ACCESS_ALLOWLIST

# آی‌دی‌های عددیِ تلگرامِ مجاز در حالت allowlist. آی‌دی عددی محرمانه نیست، پس مثل بقیه‌ی
# تنظیمات اینجا می‌ماند تا اضافه‌کردنِ یک نفر فقط یک push باشد و لازم نشود روی سرور به
# .env دست بزنیم. با ALLOWED_USER_IDSِ .env (اگر چیزی داشته باشد) جمع بسته می‌شود.
# ⚠️ این‌ها فقط دسترسیِ استفاده از ربات را می‌دهند؛ عضویت در خانوارِ مشترک کارِ لینکِ دعوت
# است (دکمه‌ی «افزودن عضو به خانوار»).
EXTRA_ALLOWED_USER_IDS = (
    429557996,
)


def load_settings() -> Settings:
    raw_ids = _get("ALLOWED_USER_IDS", "") or ""
    allowed = frozenset(
        int(x) for x in raw_ids.replace(" ", "").split(",") if x.strip()
    )
    # اگر .env لیستی داده، آی‌دی‌های کد را هم به آن اضافه کن. اگر .env خالی است یعنی ربات
    # عمداً برای همه باز است؛ آن‌وقت اضافه‌کردنِ این‌ها لیست را غیرخالی و ربات را ناگهان
    # محدود می‌کرد و بقیه (از جمله خودِ صاحبِ ربات) را بیرون می‌انداخت. پس دست نمی‌زنیم.
    if allowed:
        allowed = allowed | frozenset(EXTRA_ALLOWED_USER_IDS)
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
        chars_per_line=int(_get("CHARS_PER_LINE", str(CHARS_PER_LINE))),
        chunk_2_parts=int(_get("CHUNK_2_PARTS", str(CHUNK_2_PARTS))),
        chunk_3_parts=int(_get("CHUNK_3_PARTS", str(CHUNK_3_PARTS))),
        chunk_max_chars=int(_get("CHUNK_MAX_CHARS", str(CHUNK_MAX_CHARS))),
        max_parts=int(_get("MAX_PARTS", str(MAX_PARTS))),
        voice_oneshot_max_seconds=int(_get("VOICE_ONESHOT_MAX_SECONDS", str(VOICE_ONESHOT_MAX_SECONDS))),
        access_mode=_get("ACCESS_MODE", ACCESS_MODE),
        allowed_user_ids=allowed,
        default_currency=_get("DEFAULT_CURRENCY", DEFAULT_CURRENCY),
        reminder_hour=int(_get("REMINDER_HOUR", str(REMINDER_HOUR))),
        morning_hour=int(_get("MORNING_HOUR", str(MORNING_HOUR))),
        db_path=_get("DB_PATH", "data/pocket_cfo.db"),
        max_voice_seconds=int(_get("MAX_VOICE_SECONDS", str(MAX_VOICE_SECONDS))),
        chat_history_turns=int(_get("CHAT_HISTORY_TURNS", str(CHAT_HISTORY_TURNS))),
        rate_limit_max=int(_get("RATE_LIMIT_MAX", str(RATE_LIMIT_MAX))),
        rate_limit_window=int(_get("RATE_LIMIT_WINDOW", str(RATE_LIMIT_WINDOW))),
        daily_llm_limit=int(_get("DAILY_LLM_LIMIT", str(DAILY_LLM_LIMIT))),
        history_max_messages=int(_get("HISTORY_MAX_MESSAGES", str(HISTORY_MAX_MESSAGES))),
        max_tool_rounds=int(_get("MAX_TOOL_ROUNDS", str(MAX_TOOL_ROUNDS))),
        ingest_host=_get("INGEST_HOST", INGEST_HOST),
        ingest_port=int(_get("INGEST_PORT", str(INGEST_PORT))),
        ingest_tokens=_parse_ingest_tokens(_get("INGEST_TOKENS", "") or ""),
        ingest_max_body_bytes=int(_get("INGEST_MAX_BODY_MB", str(INGEST_MAX_BODY_MB))) * 1024 * 1024,
        ingest_audio_dir=_get("INGEST_AUDIO_DIR", INGEST_AUDIO_DIR),
        ingest_public_url=(_get("INGEST_PUBLIC_URL", INGEST_PUBLIC_URL) or "").rstrip("/"),
    )


settings = load_settings()
