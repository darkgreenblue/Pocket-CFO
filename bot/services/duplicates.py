"""تشخیصِ «احتمالاً تکراری» — کاملاً با قاعده‌ی ثابت، بدونِ هیچ کالِ LLM.

سناریوی اصلی: کاربر خرجی را با ویس گفته، بعد اسکرین‌شاتِ پیامک‌های بانک را می‌فرستد که
همان خرج هم در آن است؛ یا دو اسکرین‌شاتِ هم‌پوشان پشتِ سرِ هم.

تصمیم‌های محصولی (تأییدِ کاربر):
  • **ثبت می‌شود و فقط علامت می‌خورد** — هیچ‌چیز بی‌صدا دور ریخته نمی‌شود؛ کارت خطِ
    «⚠️ احتمالاً تکراریِ #X» و دکمه‌ی «🗑 تکراریه، حذف» می‌گیرد.
  • مبلغ **±۲٪** (تا «۲۰۰ تومن»ِ ویس با «۱۹۸٬۵۰۰»ِ پیامک جور شود). چون فقط هشدار است،
    مثبتِ کاذب یک لمس هزینه دارد.
  • همه‌ی ورودی‌ها (عکس، ویس، متن، میان‌بر، صفِ صبح) بررسی می‌شوند.
  • جمعِ کلِ رسید در برابرِ یک تراکنشِ تکی (سبدی که قبلاً با ویس گفته شده) → فقط هشدار.

قاعده‌ها:
  ۱) همان خانوار، همان واحدِ پول، مبلغِ هر دو معلوم، اختلاف ≤ ۲٪ از بزرگ‌تر.
  ۲) فاصله‌ی «تاریخِ مؤثر» ≤ ۲ روز. تاریخِ مؤثر = تاریخِ واقعیِ خرج اگر معلوم است
     (پیامکِ بانک)، وگرنه روزِ ثبت.
  ۳) ردیف‌های **همان پیام/عکس** با هم مقایسه نمی‌شوند (دو تاکسیِ ۵۰ تومنی در یک ویس
     دو خرجِ واقعی‌اند).
  ۴) یک‌به‌یک: هر تراکنشِ قبلی در هر دسته فقط یک ردیفِ تازه را تکراری می‌کند؛ پس اگر دو
     تاکسی داشتی و یکی ثبت شده بود، فقط یکی علامت می‌خورد.
  ۵) بهترین جفت: کمترین اختلافِ مبلغ، بعد نزدیک‌ترین تاریخ، بعد تازه‌ترین.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Optional

from bot.db import repo
from bot.utils.money import format_amount, to_persian_digits

logger = logging.getLogger(__name__)

WINDOW_DAYS = 2
AMOUNT_TOLERANCE = Decimal("0.02")


def effective_date(txn: dict[str, Any]) -> Optional[date]:
    """روزی که خرج واقعاً انجام شده: occurred_on اگر هست، وگرنه روزِ ثبت."""
    for key in ("occurred_on", "created_at"):
        raw = (txn.get(key) or "")[:10]
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                continue
    return None


def amounts_close(a: Any, b: Any) -> bool:
    """اختلافِ نسبی ≤ ۲٪ (نسبت به بزرگ‌تر). صفر/None هرگز جفت نمی‌شود."""
    if a is None or b is None:
        return False
    da, db = Decimal(str(a)), Decimal(str(b))
    if da <= 0 or db <= 0:
        return False
    return abs(da - db) <= max(da, db) * AMOUNT_TOLERANCE


def _same_currency(a: dict, b: dict) -> bool:
    return (a.get("currency_display") or "toman") == (b.get("currency_display") or "toman")


def _score(new: dict, cand: dict, new_day: date) -> tuple:
    da, db = Decimal(str(new["amount"])), Decimal(str(cand["amount"]))
    rel = abs(da - db) / max(da, db)
    cand_day = effective_date(cand) or new_day
    return (rel, abs((new_day - cand_day).days), -int(cand["id"]))


def find_match(new: dict[str, Any], candidates: Iterable[dict[str, Any]],
               claimed: set[int] | None = None) -> Optional[dict[str, Any]]:
    """بهترین تراکنشِ قبلی که `new` احتمالاً تکرارِ آن است (یا None). تابعِ خالص."""
    new_day = effective_date(new)
    if new.get("amount") is None or new_day is None:
        return None
    claimed = claimed or set()
    best, best_score = None, None
    for cand in candidates:
        if cand["id"] == new["id"] or cand["id"] in claimed:
            continue
        if not _same_currency(new, cand) or not amounts_close(new["amount"], cand.get("amount")):
            continue
        cand_day = effective_date(cand)
        if cand_day is None or abs((new_day - cand_day).days) > WINDOW_DAYS:
            continue
        score = _score(new, cand, new_day)
        if best_score is None or score < best_score:
            best, best_score = cand, score
    return best


def _candidates(user_id: int, days: list[date], exclude: set[int]) -> list[dict[str, Any]]:
    """تراکنش‌های خانوار که ممکن است در پنجره باشند. occurred_on ≤ created_at است، پس
    فیلترِ created_at از «اولین روز − پنجره» هیچ نامزدی را جا نمی‌اندازد."""
    start = min(days) - timedelta(days=WINDOW_DAYS)
    rows = repo.list_user_transactions(user_id, start.isoformat(), include_drafts=True)
    return [r for r in rows if r["id"] not in exclude and r.get("amount") is not None]


def flag_new(user_id: int, txn_ids: Iterable[int]) -> dict[int, int]:
    """تراکنش‌های تازه‌ی **یک** پیام/عکس را با قبلی‌ها مقایسه و علامت می‌زند.

    خروجی: {id تازه: id قبلی}. هرگز استثنا بیرون نمی‌دهد — تشخیصِ تکراری نباید ثبت یا
    ارسالِ کارت را بخواباند.
    """
    batch = [i for i in dict.fromkeys(txn_ids) if i is not None]
    if not batch:
        return {}
    try:
        news = [t for t in (repo.get_transaction(i) for i in batch) if t]
        days = [d for d in (effective_date(t) for t in news) if d]
        if not days:
            return {}
        candidates = _candidates(user_id, days, exclude=set(batch))
        claimed: set[int] = set()
        found: dict[int, int] = {}
        # مبلغ‌های بزرگ‌تر اول جفت می‌شوند تا ردیفِ کوچکِ نزدیک، جفتِ دقیقِ دیگری را نقاپد.
        for txn in sorted(news, key=lambda t: -(t.get("amount") or 0)):
            match = find_match(txn, candidates, claimed)
            if match is None:
                continue
            claimed.add(match["id"])
            found[txn["id"]] = match["id"]
            repo.set_duplicate_of(txn["id"], match["id"])
        if found:
            logger.info("duplicates: user=%s batch=%s flagged=%s", user_id, batch, found)
        return found
    except Exception:  # noqa: BLE001
        logger.exception("duplicates: بررسیِ تکراری ناموفق بود (user=%s, batch=%s)", user_id, batch)
        return {}


def receipt_total_match(user_id: int, totals: Iterable[Any], *, exclude: Iterable[int],
                        on_day: Optional[date] = None,
                        currency: str = "toman") -> Optional[dict[str, Any]]:
    """جمعِ کلِ یک رسید با یک تراکنشِ **تکیِ** قبلی جور است؟ (سبدی که قبلاً با ویس گفته شده)

    `totals` چند جمعِ ممکن است (جمعِ اقلام، مبلغِ قابلِ پرداخت). فقط هشدار، بدونِ علامت.
    """
    try:
        day = on_day or date.today()
        exclude_set = set(exclude)
        probe = [t for t in totals if t]
        if not probe:
            return None
        candidates = _candidates(user_id, [day], exclude=exclude_set)
        best = None
        for total in probe:
            new = {"id": -1, "amount": total, "currency_display": currency,
                   "created_at": datetime.combine(day, datetime.min.time()).isoformat()}
            match = find_match(new, candidates)
            if match and (best is None or match["id"] > best["id"]):
                best = match
        return best
    except Exception:  # noqa: BLE001
        logger.exception("duplicates: بررسیِ جمعِ رسید ناموفق بود (user=%s)", user_id)
        return None


def _day_word(day: Optional[date]) -> str:
    if day is None:
        return ""
    delta = (date.today() - day).days
    if delta == 0:
        return "امروز"
    if delta == 1:
        return "دیروز"
    if 1 < delta < 7:
        return f"{to_persian_digits(delta)} روز پیش"
    return to_persian_digits(day.isoformat())


def describe(original: dict[str, Any]) -> str:
    """«#123 «نان» — ۱۵۰٬۰۰۰ تومان، دیروز» برای خطِ هشدارِ کارت و پیام."""
    title = (original.get("title") or "").strip() or "بی‌عنوان"
    parts = [f"#{original['id']} «{title}»",
             format_amount(original.get("amount"), original.get("currency_display", "toman"))]
    when = _day_word(effective_date(original))
    text = " — ".join(parts)
    return f"{text}، {when}" if when else text
