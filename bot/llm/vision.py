"""ورودیِ عکس: رسیدِ خرید، اسکرین‌شاتِ پیامکِ بانکی و گردشِ حسابِ موبایل‌بانک.

تقسیمِ کار (عمداً):
  • **مدل فقط می‌خواند.** هر ردیف را با عددهایی که «همان‌طور که چاپ شده» دیده برمی‌گرداند،
    به‌علاوه‌ی واحدی که تشخیص داده (ریال/تومان) و جمع‌های چاپ‌شده‌ی پایینِ رسید.
  • **کد حساب می‌کند.** ریال→تومان (یک صفر کمتر)، تعداد×فی، کنارگذاشتنِ واریزها، پاک‌کردنِ
    برچسب‌های عمومیِ بانک از عنوان، ماهِ شمسیِ ردیف‌های قدیمی، و مقایسه‌ی جمعِ اقلام با جمعِ
    چاپ‌شده — همه قطعی و تست‌پذیرند. همان قاعده‌ی کلِ پروژه: محاسبه با سیستم، نه با LLM.

هر ردیفِ رسید یک تراکنشِ جداست (تصمیمِ محصولی). مالیات و تخفیفِ فاکتور ثبت نمی‌شوند؛ فقط
به کاربر گفته می‌شود که مبلغِ قابلِ پرداخت چقدر بوده.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import jdatetime

from bot.services import tags as tags_service
from bot.utils import jalali
from bot.utils.money import format_amount, normalize_digits, parse_amount, to_persian_digits

logger = logging.getLogger(__name__)

KIND_RECEIPT = "receipt"
KIND_BANK_SMS = "bank_sms"
KIND_BANK_APP = "bank_app"
KIND_SLIP = "payment_slip"
KIND_NOT_FINANCIAL = "not_financial"
KIND_UNREADABLE = "unreadable"
KNOWN_KINDS = {KIND_RECEIPT, KIND_BANK_SMS, KIND_BANK_APP, KIND_SLIP,
               KIND_NOT_FINANCIAL, KIND_UNREADABLE}
BANK_KINDS = {KIND_BANK_SMS, KIND_BANK_APP}

FOREIGN_UNITS = {"usd", "eur", "usdt", "aed", "try", "btc"}

# بیشتر از این ردیف یعنی احتمالاً مدل توهم زده (یا عکس فهرستِ خیلی بلندی است)؛ چت را با
# ده‌ها کارت پر نمی‌کنیم و به کاربر می‌گوییم بقیه را جدا بفرستد.
MAX_LINES = 40

# اختلافِ مجاز بین جمعِ اقلام و جمعِ چاپ‌شده (گردکردنِ ردیف‌های وزنی)؛ یک قلمِ جاافتاده
# تقریباً همیشه بیشتر از این است.
TOTAL_TOLERANCE = 0.003

# برچسب‌های عمومیِ بانک «نامِ خرج» نیستند. اگر مدل با وجودِ پرامپت یکی از این‌ها را عنوان
# گذاشت، کد پاکش می‌کند تا کارت «نیازمند تکمیل: عنوان» بگیرد و کاربر اسمِ واقعی را بدهد.
GENERIC_BANK_LABELS = {
    tags_service.normalize(x) for x in (
        "برداشت", "برداشت وجه", "برداشت از حساب", "برداشت از کارت", "خرید", "خرید کالا",
        "خرید کارتخوان", "خرید اینترنتی", "خرید پایانه", "انتقال", "انتقال وجه",
        "کارت به کارت", "انتقال کارت به کارت", "پایا", "ساتنا", "پل", "پرداخت",
        "پرداخت قبض", "پرداخت اینترنتی", "واریز", "تراکنش", "شتاب", "نامشخص",
    )
}


@dataclass
class VisionLine:
    """یک تراکنشِ آماده‌ی ثبت (مبلغ به تومان، یا ارزِ خارجی همان‌طور که بود)."""
    title: Optional[str]
    amount: Optional[Any]
    currency: str
    note: str = ""
    raw: str = ""
    suggested_tags: list[str] = field(default_factory=list)
    period: Optional[tuple[int, int]] = None       # (سال، ماه) شمسی اگر مالِ ماهِ گذشته است
    occurred_on: Optional[str] = None              # تاریخِ واقعیِ ردیف (میلادی YYYY-MM-DD) برای تشخیصِ تکراری
    source_amount: Optional[Any] = None            # عددِ روی عکس (برای کنترلِ جمع)


@dataclass
class TotalsCheck:
    status: str                    # ok | mismatch | unverifiable
    items_sum: Optional[Any] = None    # به تومان (یا ارزِ اصلی)
    printed: Optional[Any] = None      # به تومان (یا ارزِ اصلی)


@dataclass
class VisionReading:
    kind: str
    merchant: Optional[str]
    source_unit: str
    unit_reason: str
    lines: list[VisionLine]
    deposits_ignored: int = 0
    dropped_over_limit: int = 0
    payable: Optional[Any] = None      # مبلغِ قابلِ پرداختِ رسید (تبدیل‌شده)
    has_tax_or_discount: bool = False
    check: Optional[TotalsCheck] = None
    model_reply: str = ""

    @property
    def converted_from_rial(self) -> bool:
        return self.source_unit == "rial"


# ---------- ساختِ پیامِ مدل ----------

def image_part(image: bytes, mime: str = "image/jpeg") -> dict:
    """تصویر به‌صورت data URI — قالبی که OpenRouter برای جمنای می‌پذیرد."""
    b64 = base64.b64encode(image).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def user_content(image: bytes, mime: str, caption: str = "") -> list[dict]:
    text = "این عکس را طبق قوانین بخوان و JSON بده."
    if caption.strip():
        text += f"\nکاربر زیرِ عکس نوشته: «{caption.strip()}» (اگر درباره‌ی واحد/ماه/فروشگاه است، رعایتش کن)."
    return [{"type": "text", "text": text}, image_part(image, mime)]


# ---------- parse خروجیِ مدل ----------

def loads(raw: str) -> dict[str, Any]:
    """JSONِ مدل را با تحملِ ```/متنِ اضافه می‌خواند؛ شکست → دیکشنریِ «ناخوانا»."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    logger.error("vision: خروجیِ مدل JSON نبود: %s", (raw or "")[:300])
    return {"kind": KIND_UNREADABLE, "items": [], "reply": ""}


def _num(value: Any) -> Optional[Any]:
    """عددِ مثبت از JSONِ مدل (عدد، یا رشته با ارقامِ فارسی/جداکننده). نامعتبر → None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = abs(value)
    elif isinstance(value, str):
        v = parse_amount(value)
        if v is None:
            return None
    else:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _unit(data: dict, kind: str) -> str:
    unit = _text(data.get("unit")).lower()
    if unit in ("rial", "ریال", "irr"):
        return "rial"
    if unit in ("toman", "تومان", "تومن"):
        return "toman"
    if unit in FOREIGN_UNITS:
        return unit
    # مدل واحد نداد: پیامک/موبایل‌بانک و رسیدهای ایرانی تقریباً همیشه ریال‌اند.
    logger.warning("vision: واحد مشخص نشد (kind=%s, unit=%r) → ریال فرض شد", kind, data.get("unit"))
    return "rial"


def convert(amount: Optional[Any], unit: str) -> tuple[Optional[Any], str]:
    """مبلغِ روی عکس → (مبلغِ ثبت، واحدِ ثبت). ریال ÷ ۱۰ و گرد به تومانِ صحیح."""
    if amount is None:
        return None, "toman" if unit in ("rial", "toman") else unit
    if unit == "rial":
        toman = (Decimal(str(amount)) / Decimal(10)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(toman), "toman"
    if unit == "toman":
        return int(Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP)), "toman"
    return amount, unit


def line_amount(item: dict) -> Optional[Any]:
    """مبلغِ کلِ ردیف به واحدِ روی عکس: ستونِ مبلغ، وگرنه تعداد × فی، وگرنه خودِ فی."""
    total = _num(item.get("line_total"))
    if total is not None:
        return total
    qty, price = _num(item.get("quantity")), _num(item.get("unit_price"))
    if price is not None and qty is not None:
        product = Decimal(str(qty)) * Decimal(str(price))
        return int(product.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return price


def clean_title(name: Any) -> Optional[str]:
    """نامِ قلم؛ برچسبِ عمومیِ بانک («برداشت»، «خرید»…) نام نیست → None."""
    title = _text(name)
    if not title or tags_service.normalize(title) in GENERIC_BANK_LABELS:
        return None
    return title


def _jalali_ym(date_text: Any) -> Optional[tuple[int, int]]:
    """«1405/07/09» یا «۱۴۰۵-۰۷-۰۹» → (1405, 7)."""
    text = normalize_digits(_text(date_text)).replace("-", "/").replace(".", "/")
    parts = [p for p in text.split("/") if p.strip().isdigit()]
    if len(parts) < 2:
        return None
    year, month = int(parts[0]), int(parts[1])
    if year < 100:                       # «05/07/09» → 1405
        year += 1400
    if not (1300 <= year <= 1500 and 1 <= month <= 12):
        return None
    return year, month


def occurred_on(date_text: Any) -> Optional[str]:
    """«1405/07/09» → «2026-10-01» (میلادی، برای مقایسه با created_at). نامعتبر/آینده → None."""
    text = normalize_digits(_text(date_text)).replace("-", "/").replace(".", "/")
    parts = [p for p in text.split("/") if p.strip().isdigit()]
    if len(parts) < 3:
        return None
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    if year < 100:
        year += 1400
    try:
        greg = jdatetime.date(year, month, day).togregorian()
    except ValueError:
        return None
    # یک روز تحمل: ساعتِ سرور (UTC) ممکن است هنوز «دیروزِ» تهران باشد.
    if greg > date.today() + timedelta(days=1):
        return None
    return greg.isoformat()


def _past_period(date_text: Any) -> Optional[tuple[int, int]]:
    """فقط اگر تاریخ مالِ یک ماهِ **گذشته** (حداکثر یک سال قبل) است؛ ماهِ جاری/آینده → None."""
    ym = _jalali_ym(date_text)
    if ym is None:
        return None
    current = jalali.current_ym()
    if ym >= current or ym[0] < current[0] - 1:
        return None
    return ym


def _qty_text(qty: Any) -> str:
    return to_persian_digits(qty if not isinstance(qty, float) else f"{qty:g}")


def _note(item: dict, kind: str, merchant: str, unit: str) -> str:
    parts: list[str] = []
    if kind in BANK_KINDS:
        desc = _text(item.get("description"))
        if desc:
            parts.append(desc)
        when = " ".join(x for x in (_text(item.get("date")), _text(item.get("time"))) if x)
        if when:
            parts.append(to_persian_digits(when))
        if merchant:
            parts.append(merchant)
    else:
        qty, price = _num(item.get("quantity")), _num(item.get("unit_price"))
        if qty is not None and price is not None and qty != 1:
            shown, cur = convert(price, unit)
            parts.append(f"{_qty_text(qty)} × {format_amount(shown, cur)}")
        if merchant:
            parts.append(merchant)
    return " — ".join(parts)


def normalize(data: dict[str, Any]) -> VisionReading:
    """خروجیِ خامِ مدل → ردیف‌های آماده‌ی ثبت + کنترلِ جمع. کاملاً قطعی (بدون LLM)."""
    kind = _text(data.get("kind")).lower()
    if kind not in KNOWN_KINDS:
        kind = KIND_RECEIPT if data.get("items") else KIND_UNREADABLE
    merchant = _text(data.get("merchant"))
    unit = _unit(data, kind)
    caption_period = jalali.resolve_past_month(_text(data.get("past_month")))
    if caption_period is not None and caption_period >= jalali.current_ym():
        caption_period = None
    receipt_period = _past_period(data.get("date"))

    reading = VisionReading(kind=kind, merchant=merchant or None, source_unit=unit,
                            unit_reason=_text(data.get("unit_reason")), lines=[],
                            model_reply=_text(data.get("reply")))
    if kind in (KIND_NOT_FINANCIAL, KIND_UNREADABLE):
        return reading

    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        direction = _text(item.get("direction")).lower()
        if kind in BANK_KINDS and direction == "in":
            reading.deposits_ignored += 1        # واریز خرج نیست
            continue
        source_amount = line_amount(item)
        amount, currency = convert(source_amount, unit)
        title = clean_title(item.get("name"))
        raw = _text(item.get("raw"))[:120]
        if title is None and amount is None and not raw:
            continue                             # ردیفِ کاملاً خالی، چیزی برای کارت ندارد
        if len(reading.lines) >= MAX_LINES:
            reading.dropped_over_limit += 1
            continue
        tags = [str(t) for t in (item.get("suggested_tags") or []) if str(t).strip()]
        reading.lines.append(VisionLine(
            title=title, amount=amount, currency=currency,
            note=_note(item, kind, merchant, unit), raw=raw, suggested_tags=tags,
            period=caption_period or _past_period(item.get("date")) or receipt_period,
            source_amount=source_amount,
            occurred_on=occurred_on(item.get("date")) or occurred_on(data.get("date")),
        ))

    if kind == KIND_RECEIPT:
        _check_totals(reading, data, unit)
    return reading


def _close(a: Any, b: Any) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal(str(b)) * Decimal(str(TOTAL_TOLERANCE))


def _check_totals(reading: VisionReading, data: dict, unit: str) -> None:
    """جمعِ اقلامِ خوانده‌شده را با جمعِ چاپ‌شده مقایسه می‌کند — نگهبانِ «قلمِ جاافتاده»."""
    items_total = _num(data.get("items_total"))
    payable = _num(data.get("payable_total"))
    discount = _num(data.get("discount_total")) or 0
    tax = _num(data.get("tax_total")) or 0
    reading.has_tax_or_discount = bool(discount or tax)
    if payable is not None:
        reading.payable = convert(payable, unit)[0]

    amounts = [ln.source_amount for ln in reading.lines]
    if not amounts:
        return
    if any(a is None for a in amounts):
        reading.check = TotalsCheck(status="unverifiable")
        return
    items_sum = sum(Decimal(str(a)) for a in amounts)

    if items_total is not None:
        expected = items_total
        ok = _close(items_sum, items_total)
    elif payable is not None:
        # اقلام یا خالص‌اند (= قابل پرداخت) یا قبل از تخفیف/مالیات (= پرداختی + تخفیف − مالیات).
        expected = Decimal(str(payable)) + Decimal(str(discount)) - Decimal(str(tax))
        ok = _close(items_sum, expected) or _close(items_sum, payable)
    else:
        return
    reading.check = TotalsCheck(
        status="ok" if ok else "mismatch",
        items_sum=convert(items_sum, unit)[0],
        printed=convert(expected, unit)[0],
    )
    log = logger.info if ok else logger.warning
    log("vision: کنترل جمع رسید — اقلام=%s چاپ‌شده=%s واحد=%s نتیجه=%s",
        items_sum, expected, unit, reading.check.status)


# ---------- پیام به کاربر (از روی آنچه واقعاً ثبت شد) ----------

_KIND_HEADLINE = {
    KIND_RECEIPT: "🧾 رسید",
    KIND_SLIP: "🧾 رسیدِ پرداخت",
    KIND_BANK_SMS: "🏦 پیامک‌های بانکی",
    KIND_BANK_APP: "🏦 گردش حساب",
}

NOT_FINANCIAL_REPLY = ("🖼 توی این عکس خرج یا پرداختی ندیدم. می‌تونی عکسِ رسیدِ خرید، اسکرین‌شاتِ "
                       "پیامک‌های بانکی یا گردشِ حسابِ موبایل‌بانک بفرستی 🙂")
UNREADABLE_REPLY = ("🖼 عکس رو نتونستم مطمئن بخونم (تار، بریده یا کج بود). لطفاً از نزدیک‌تر و با "
                    "نورِ بهتر دوباره بگیر، یا به‌صورت «فایل» بفرست که کیفیتش کم نشه.")
NOTHING_FOUND_REPLY = "🖼 عکس رو خوندم ولی هیچ خرجی توش پیدا نکردم که ثبت بشه."


def _fa_count(n: int) -> str:
    return to_persian_digits(n)


def summary(reading: VisionReading, created: int, incomplete: int,
            basket_match: Optional[str] = None) -> str:
    """متنِ پاسخ — از روی تعدادِ واقعیِ ثبت‌شده‌ها، نه از حرفِ مدل.

    `basket_match`: توصیفِ تراکنشِ تکیِ قبلی‌ای که جمعش با کلِ این رسید یکی است.
    """
    if reading.kind == KIND_NOT_FINANCIAL:
        return NOT_FINANCIAL_REPLY
    if reading.kind == KIND_UNREADABLE:
        return UNREADABLE_REPLY

    lines: list[str] = []
    if created:
        head = _KIND_HEADLINE.get(reading.kind, "🖼 عکس")
        if reading.merchant and reading.kind in (KIND_RECEIPT, KIND_SLIP):
            head += f" «{reading.merchant}»"
        unit_word = "قلم" if reading.kind == KIND_RECEIPT else "برداشت" \
            if reading.kind in BANK_KINDS else "مورد"
        head += f" — {_fa_count(created)} {unit_word} ثبت شد"
        lines.append(head + "؛ کارت‌ها پایین 👇")
    else:
        lines.append(NOTHING_FOUND_REPLY)

    if created and reading.converted_from_rial:
        lines.append("💱 مبلغ‌های روی عکس ریالی بود؛ به تومان تبدیل شد (یک صفر کم شد).")
    if incomplete:
        if reading.kind in BANK_KINDS:
            lines.append(f"✏️ {_fa_count(incomplete)} مورد اسم ندارن — بگو هرکدوم برای چی بوده "
                         "(با دکمه‌ی «✏️ عنوان» روی کارت یا ریپلای).")
        else:
            lines.append(f"✏️ {_fa_count(incomplete)} مورد ناقص خونده شد؛ روی کارتشون کامل کن.")
    if reading.deposits_ignored:
        lines.append(f"ℹ️ {_fa_count(reading.deposits_ignored)} واریز هم دیدم که خرج نیست و "
                     "ثبتش نکردم.")
    if reading.dropped_over_limit:
        lines.append(f"⚠️ عکس بیشتر از {_fa_count(MAX_LINES)} ردیف داشت؛ "
                     f"{_fa_count(reading.dropped_over_limit)} ردیفِ آخر ثبت نشد — "
                     "اون قسمت رو جدا عکس بگیر.")

    check = reading.check
    if check and check.status == "mismatch":
        lines.append(
            f"⚠️ جمعِ اقلامی که خوندم ({format_amount(check.items_sum)}) با جمعِ چاپ‌شده‌ی رسید "
            f"({format_amount(check.printed)}) نمی‌خونه — احتمالاً یه قلم جا افتاده یا اشتباه "
            "خونده شده. لطفاً کارت‌ها رو با رسید چک کن."
        )
    if created and basket_match:
        lines.append(f"⚠️ جمعِ این رسید با تراکنشِ {basket_match} یکیه — اگه همون خریده، "
                     "یا اون یکی رو پاک کن یا کارت‌های این رسید رو.")
    if created and reading.has_tax_or_discount and reading.payable is not None:
        lines.append(f"ℹ️ مالیات/تخفیفِ رسید جدا ثبت نشد؛ مبلغِ قابلِ پرداختِ رسید "
                     f"{format_amount(reading.payable)} بود.")
    return "\n".join(lines)


def describe(reading: VisionReading, caption: str = "") -> str:
    """یک خطِ کوتاه برای حافظه‌ی گفتگو («کاربر چه فرستاد»)."""
    kind_fa = {KIND_RECEIPT: "رسید", KIND_SLIP: "رسید پرداخت", KIND_BANK_SMS: "پیامک بانکی",
               KIND_BANK_APP: "گردش حساب", KIND_NOT_FINANCIAL: "غیرمالی",
               KIND_UNREADABLE: "ناخوانا"}.get(reading.kind, "عکس")
    text = f"[عکس: {kind_fa}"
    if reading.merchant:
        text += f" «{reading.merchant}»"
    if reading.lines:
        text += f"، {_fa_count(len(reading.lines))} ردیف"
    text += "]"
    if caption.strip():
        text += f" {caption.strip()}"
    return text
