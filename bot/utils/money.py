"""کار با مبلغ: نرمال‌سازی ارقام، parse، فرمت با جداکننده و واحد پول.

واحد پول هر تراکنش یک «کد» است (مثل toman/rial/usd/...). اگر کاربر در ویس واحدی
نگفته باشد، LLM واحد را null برمی‌گرداند و کد، واحد پیش‌فرض تنظیمات را اعمال می‌کند.
اگر واحدی صریحاً ذکر شده باشد، همان ثبت می‌شود.
"""
from __future__ import annotations

from typing import Optional

# نگاشت ارقام فارسی/عربی به لاتین
_DIGIT_MAP = {
    ord("۰"): "0", ord("۱"): "1", ord("۲"): "2", ord("۳"): "3", ord("۴"): "4",
    ord("۵"): "5", ord("۶"): "6", ord("۷"): "7", ord("۸"): "8", ord("۹"): "9",
    ord("٠"): "0", ord("١"): "1", ord("٢"): "2", ord("٣"): "3", ord("٤"): "4",
    ord("٥"): "5", ord("٦"): "6", ord("٧"): "7", ord("٨"): "8", ord("٩"): "9",
}

_LATIN_TO_PERSIAN = {ord(str(d)): "۰۱۲۳۴۵۶۷۸۹"[d] for d in range(10)}

# کدهای شناخته‌شده‌ی واحد پول و برچسب فارسی‌شان
CURRENCY_LABELS = {
    "toman": "تومان",
    "rial": "ریال",
    "usd": "دلار",
    "eur": "یورو",
    "usdt": "تتر",
    "btc": "بیت‌کوین",
    "aed": "درهم",
    "try": "لیر",
}


def normalize_digits(text: str) -> str:
    """ارقام فارسی/عربی را به لاتین تبدیل می‌کند."""
    return (text or "").translate(_DIGIT_MAP)


def to_persian_digits(text: str) -> str:
    return str(text).translate(_LATIN_TO_PERSIAN)


# جداکننده‌ی هزارگان همیشه هزارگان است؛ ولی «.» و «٫» و «/» می‌توانند اعشار باشند.
# («/» چون روی کیبورد فارسی ممیزِ رایج است: ۱/۴ یعنی ۱.۴)
_THOUSANDS_CHARS = ",٬'’ "
_DECIMAL_CHARS = ".٫/"


def parse_amount(text: str):
    """از ورودی کاربر یک مبلغ بیرون می‌کشد: int، یا float اگر واقعاً اعشاری باشد.

    ⚠️ اعشار برای ارز خارجی حیاتی است: «۱.۴ دلار» باید ۱.۴ بماند، نه ۱۴. پس نمی‌شود
    مثل قبل همه‌ی غیررقم‌ها را دور ریخت.

    قاعده‌ی تفکیک (هم‌راستا با نحوه‌ی نوشتنِ واقعیِ آدم‌ها):
    — «,» و «٬» همیشه هزارگان‌اند.
    — «.» / «٫» / «/» فقط وقتی اعشارند که **یک‌بار** آمده باشند و بعدشان ۱ یا ۲ رقم باشد.
      پس «۱.۴»→۱.۴ و «۱۲.۷۵»→۱۲.۷۵، ولی «۱.۵۰۰»→۱۵۰۰ و «۲۵۰.۰۰۰»→۲۵۰۰۰۰.
    اگر هیچ رقمی نباشد ``None``.
    """
    if not text:
        return None
    raw = normalize_digits(text)
    for ch in _THOUSANDS_CHARS:
        raw = raw.replace(ch, "")

    # فقط رقم و جداکننده‌های اعشاری را نگه می‌داریم (واحد پول، ایموجی و… دور ریخته می‌شوند).
    kept = "".join(ch for ch in raw if ch.isdigit() or ch in _DECIMAL_CHARS)
    seps = [ch for ch in kept if ch in _DECIMAL_CHARS]
    digits_only = "".join(ch for ch in kept if ch.isdigit())
    if not digits_only:
        return None

    if len(seps) == 1:
        whole, _, frac = kept.partition(seps[0])
        whole_digits = "".join(ch for ch in whole if ch.isdigit())
        frac_digits = "".join(ch for ch in frac if ch.isdigit())
        if frac_digits and len(frac_digits) <= 2:
            value = float(f"{whole_digits or '0'}.{frac_digits}")
            return int(value) if value.is_integer() else value

    return int(digits_only)


def group_digits(value: int) -> str:
    """عدد را با جداکننده‌ی هزارگان فارسی (٬) و ارقام فارسی برمی‌گرداند."""
    grouped = f"{int(value):,}".replace(",", "٬")
    return to_persian_digits(grouped)


def _fmt_number(value) -> str:
    """عدد صحیح یا اعشاری را با جداکننده فارسی فرمت می‌کند (اعشار با ٫)."""
    if isinstance(value, float) and not value.is_integer():
        intp, frac = f"{value:,.2f}".split(".")
        return to_persian_digits(intp.replace(",", "٬") + "٫" + frac)
    return group_digits(int(value))


def currency_label(currency: str) -> str:
    """برچسب نمایشی واحد پول؛ اگر ناشناخته بود خودِ کد را نشان می‌دهد."""
    if not currency:
        return CURRENCY_LABELS["toman"]
    return CURRENCY_LABELS.get(currency.lower(), currency)


def format_amount(value, currency: str = "toman") -> str:
    """مبلغ را به صورت «۲۵۰٬۰۰۰ تومان» یا «۱۲٫۷۳ دلار» برمی‌گرداند."""
    if value is None:
        return "— (تعیین‌نشده)"
    return f"{_fmt_number(value)} {currency_label(currency)}"


def to_rial(value: Optional[int], currency: str) -> int:
    """مبلغ را برای جمع‌بندی گزارش‌های ریالی تبدیل می‌کند.

    فقط toman/rial قابل تبدیل‌اند؛ واحدهای دیگر (دلار و...) جداگانه حساب می‌شوند
    و اینجا صفر برمی‌گردانند تا با ریال قاتی نشوند.
    """
    if value is None:
        return 0
    cur = (currency or "toman").lower()
    if cur == "toman":
        return int(value) * 10
    if cur == "rial":
        return int(value)
    return 0
