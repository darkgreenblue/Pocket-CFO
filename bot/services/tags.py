"""اعتبارسنجی و تطبیق تگ — «مدل پیشنهاد می‌دهد، کد تصمیم می‌گیرد».

خروجی LLM هیچ‌وقت معتبر فرض نمی‌شود. هر تگ پیشنهادی از این مسیر عبور می‌کند:
نرمال‌سازی → تطبیق دقیق/مترادف (alias) → تطبیق فازی → در غیر این صورت «پیشنهاد».
"""
from __future__ import annotations

from typing import Any

try:  # تطبیق فازی سریع‌تر و دقیق‌تر
    from rapidfuzz import fuzz

    def _ratio(a: str, b: str) -> float:
        return float(fuzz.ratio(a, b))
except ImportError:  # فال‌بک به کتابخانه‌ی استاندارد
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


DEFAULT_THRESHOLD = 85.0


def normalize(text: str) -> str:
    """نرمال‌سازی متن فارسی: ی/ك عربی، نیم‌فاصله، فاصله‌های اضافی، حروف کوچک."""
    s = (text or "").strip().lower()
    s = s.replace("ك", "ک").replace("ي", "ی").replace("ﮐ", "ک")
    s = s.replace("‌", " ").replace("‏", "").replace("‎", "")
    return " ".join(s.split())


def reconcile(
    suggested: list[str],
    tag_bank: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[int], list[str], list[str]]:
    """تگ‌های پیشنهادی LLM را با بانک تگ تطبیق می‌دهد.

    خروجی: (آی‌دی تگ‌های تطبیق‌خورده، نام آن‌ها، نام‌های تطبیق‌نخورده).
    تگ تطبیق‌نخورده هیچ‌وقت به تراکنش اعمال نمی‌شود؛ به صف پیشنهاد می‌رود.
    """
    matched_ids: list[int] = []
    matched_names: list[str] = []
    unmatched: list[str] = []

    for raw in suggested:
        norm = normalize(raw)
        if not norm:
            continue

        best_tag: dict[str, Any] | None = None
        best_score = 0.0
        for tag in tag_bank:
            candidates = [normalize(tag["name"])] + [normalize(a) for a in tag.get("aliases", [])]
            for cand in candidates:
                if not cand:
                    continue
                score = 100.0 if cand == norm else _ratio(cand, norm)
                if score > best_score:
                    best_score, best_tag = score, tag
            if best_score >= 100.0:
                break

        if best_tag is not None and best_score >= threshold:
            if best_tag["id"] not in matched_ids:
                matched_ids.append(best_tag["id"])
                matched_names.append(best_tag["name"])
        else:
            unmatched.append(raw.strip())

    return matched_ids, matched_names, unmatched


def allowed_tag_names(tag_bank: list[dict[str, Any]]) -> list[str]:
    """فهرست نام تگ‌های مجاز برای قراردادن در پرامپت LLM."""
    return [t["name"] for t in tag_bank]
