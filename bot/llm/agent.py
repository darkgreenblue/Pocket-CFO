"""هسته‌ی مکالمه:
- فاز ۱: استخراج ساختاریافته از متن و/یا چند ویس در **یک** درخواست
  (پاسخ + transcript + تراکنش‌های جدید + ویرایش‌ها + needs_data)
- فاز ۲ (فقط اگر سؤال دیتایی بود): پاسخ با ابزارهای محاسباتی/خواندنی
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from bot.config import settings
from bot.llm.client import chat
from bot.llm.prompts import EXTRACT_SYSTEM, PROFILE_BLOCK, QUERY_SYSTEM
from bot.llm.tools import TOOLS_SPEC, dispatch
from bot.services import debts as debts_service
from bot.services import goals as goals_service
from bot.services import household as household_service
from bot.services import transactions as txn_service
from bot.utils import jalali

logger = logging.getLogger(__name__)

NO_GOAL_PERMISSION = (
    "🔒 هدف‌گذاری در این خانوار برای اکانتِ تو فعال نیست، برای همین هدف ثبت نشد. "
    "اگر لازم است، از عضوی که خانوار را ساخته بخواه دسترسی‌ات را باز کند."
)


@dataclass
class AgentResult:
    reply: str
    transcript: str = ""
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    goals_created: list[int] = field(default_factory=list)
    goals_updated: list[int] = field(default_factory=list)
    debts_created: list[int] = field(default_factory=list)
    debts_updated: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # فقط در حالتِ only_transactions پر می‌شود: چیزهایی که مدل دید ولی عمداً ثبت نشدند.
    dropped_kinds: list[str] = field(default_factory=list)


async def transcribe(audio_ogg: bytes, audio_format: str = "ogg") -> str:
    """فقط رونویسی (برای ویس بلند که بعد وارد خط لوله‌ی چانکینگ می‌شود)."""
    messages = [
        {"role": "system", "content": "تو فقط رونویس هستی. متن کامل گفته‌ی فارسی کاربر را بدون توضیح برگردان."},
        {"role": "user", "content": [
            {"type": "text", "text": "این پیام صوتی را دقیق رونویسی کن."},
            _audio_part(audio_ogg, audio_format),
        ]},
    ]
    msg = await chat(messages)
    return (msg.content or "").strip()


def _audio_part(audio_bytes: bytes, audio_format: str = "ogg") -> dict:
    """ویسِ تلگرام همیشه ogg است؛ ویسِ شرتکاتِ iOS معمولاً m4a."""
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    return {"type": "input_audio", "input_audio": {"data": b64, "format": audio_format}}


def _history_messages(history: Optional[list[dict]]) -> list[dict]:
    out = []
    for m in history or []:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _loads_lenient(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    logger.error("نتوانستم خروجی استخراج را parse کنم: %s", (raw or "")[:300])
    return {"reply": "", "transcript": "", "transactions": [], "updates": [],
            "debts": [], "debt_updates": [], "needs_data": False}


async def _run_extraction(
    *,
    user_content: Union[str, list],
    has_audio: bool,
    user_id: int,
    history: Optional[list[dict]],
    profile: str,
    allowed_tags: list[str],
    only_transactions: bool = False,
    source: str = "chat",
) -> AgentResult:
    system = EXTRACT_SYSTEM.format(
        tags="، ".join(allowed_tags), default_currency=settings.default_currency,
        today=jalali.today_str(),
    )
    if profile.strip():
        system += PROFILE_BLOCK.format(profile=profile.strip())

    messages = [{"role": "system", "content": system}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_content})

    # با ورودی صوتی، response_format=json را به کار نمی‌بریم و با پرامپت + parse نرم پیش می‌رویم.
    raw = await chat(messages, json_mode=not has_audio)
    data = _loads_lenient(raw.content or "")

    created: list[int] = []
    for item in data.get("transactions") or []:
        try:
            created.append(txn_service.create_from_item(
                user_id, item, transcript=data.get("transcript", ""), source=source))
        except Exception:  # noqa: BLE001
            logger.exception("ساخت تراکنش ناموفق بود")

    # درِ دومِ ورودی فقط «ثبتِ تراکنش» را می‌پذیرد: ویرایش، بدهی/طلب و هدف از این مسیر
    # روی دیتابیس اثر نمی‌گذارند (چون کاربر آنجا کارت و تأییدی جلوی چشمش ندارد).
    if only_transactions:
        dropped: list[str] = []
        if data.get("updates"):
            dropped.append("ویرایشِ تراکنشِ قبلی")
        if data.get("debts") or data.get("debt_updates"):
            dropped.append("بدهی/طلب")
        if data.get("goals") or data.get("goal_updates"):
            dropped.append("هدف")
        return AgentResult(
            reply=(data.get("reply") or "").strip() or "ثبت شد ✅",
            transcript=(data.get("transcript") or "").strip(),
            created=created, dropped_kinds=dropped,
        )

    updated: list[int] = []
    for upd in data.get("updates") or []:
        tid = upd.get("transaction_id")
        if tid:
            res = txn_service.apply_update(user_id, int(tid), upd)
            if res is not None:
                updated.append(res)

    debts_created: list[int] = []
    debts_updated: list[int] = []
    for d in data.get("debts") or []:
        if not any((d.get("counterparty"), d.get("amount") is not None, d.get("title"))):
            continue
        try:
            did, is_new = debts_service.create_or_update_from_item(user_id, d)
        except Exception:  # noqa: BLE001
            logger.exception("ثبت بدهی/طلب ناموفق بود")
            continue
        if did is None:
            continue
        (debts_created if is_new else debts_updated).append(did)

    for du in data.get("debt_updates") or []:
        did = du.get("debt_id")
        if did:
            res = debts_service.apply_update(user_id, int(did), du)
            if res is not None and res not in debts_updated:
                debts_updated.append(res)

    notes: list[str] = []
    goal_items = [g for g in (data.get("goals") or [])
                  if (g.get("topic") or "").strip() or g.get("limit_amount") is not None]
    goal_update_items = [gu for gu in (data.get("goal_updates") or []) if gu.get("goal_id")]
    may_set_goals = household_service.can_set_goals(user_id)
    if (goal_items or goal_update_items) and not may_set_goals:
        notes.append(NO_GOAL_PERMISSION)

    goals_created: list[int] = []
    goals_updated: list[int] = []
    if may_set_goals:
        for g in goal_items:
            gid = goals_service.create_or_update_from_item(user_id, g)
            if gid is not None:
                goals_created.append(gid)
        for gu in goal_update_items:
            res = goals_service.apply_update(user_id, int(gu["goal_id"]), gu)
            if res is not None:
                goals_updated.append(res)

    reply = (data.get("reply") or "").strip()
    transcript = (data.get("transcript") or "").strip()

    if data.get("needs_data"):
        answer = await _answer_data(transcript or (user_content if isinstance(user_content, str) else ""),
                                    history, user_id)
        if answer:
            reply = f"{reply}\n\n{answer}".strip() if reply else answer

    return AgentResult(reply=reply or "باشه 🙂", transcript=transcript,
                       created=created, updated=updated,
                       goals_created=goals_created, goals_updated=goals_updated,
                       debts_created=debts_created, debts_updated=debts_updated,
                       notes=notes)


async def converse(*, user_text: str, user_id: int, history=None, profile: str = "",
                   allowed_tags=None, context_note: str = "") -> AgentResult:
    """ورودی متنی."""
    content = f"{context_note}\n\n{user_text}" if context_note else user_text
    return await _run_extraction(user_content=content, has_audio=False, user_id=user_id,
                                 history=history, profile=profile, allowed_tags=allowed_tags or [])


async def converse_audio(*, audio_ogg: bytes, user_id: int, history=None, profile: str = "",
                         allowed_tags=None, context_note: str = "") -> AgentResult:
    """ورودی یک ویس — رونویسی و استخراج در یک کال."""
    instruction = "این پیام صوتی فارسی را رونویسی کن و طبق قوانین، خرج‌ها را استخراج کن."
    if context_note:
        instruction = f"{context_note}\n{instruction}"
    content = [{"type": "text", "text": instruction}, _audio_part(audio_ogg)]
    return await _run_extraction(user_content=content, has_audio=True, user_id=user_id,
                                 history=history, profile=profile, allowed_tags=allowed_tags or [])


async def converse_expense_only(*, user_text: str = "", audio: Optional[tuple[bytes, str]] = None,
                                user_id: int, profile: str = "",
                                allowed_tags=None) -> AgentResult:
    """ورودیِ درِ دوم (شرتکاتِ iOS): فقط خرج‌ها استخراج و ثبت می‌شوند.

    عمداً بدون روتر است — هر پیامی که از این مسیر می‌آید نیتِ «ثبتِ خرج» دارد؛ سؤال و
    گفتگو و گزارش جای خودشان در تلگرام است.

    ⚠️ و عمداً **بدون تاریخچه**: این مسیر یک‌شات است و هیچ کانتکستی لازم ندارد. وقتی
    خرج‌های قبلیِ همان روز به‌عنوان پیام‌های کاربر جلوی مدل گذاشته می‌شدند، مدل کلِ آن
    فهرست را «خرج‌هایی برای ثبت» می‌دید و همه را دوباره می‌ساخت — ششمین ثبتِ روز، شش
    کارتِ تازه تولید می‌کرد. نبودِ تاریخچه ریشه‌ی آن باگ را می‌زند، نه علائمش را.
    """
    instruction = (
        "کاربر این را از میان‌برِ گوشی‌اش فرستاده و فقط قصدِ ثبتِ خرج دارد. "
        "فقط خرج‌ها را در transactions بگذار؛ سؤال نپرس و گزارش نده."
    )
    content: Union[str, list]
    if audio is not None:
        blob, fmt = audio
        content = [
            {"type": "text", "text": f"{instruction} این پیام صوتی فارسی را رونویسی کن و "
                                     "خرج‌هایش را استخراج کن."},
            _audio_part(blob, fmt),
        ]
        if user_text.strip():
            content.append({"type": "text", "text": user_text.strip()})
    else:
        content = f"{instruction}\n\n{user_text}"
    return await _run_extraction(user_content=content, has_audio=audio is not None,
                                 user_id=user_id, history=None, profile=profile,
                                 allowed_tags=allowed_tags or [], only_transactions=True,
                                 source="shortcut")


async def converse_batch(*, text_parts: list[str], audio_items: list[tuple[bytes, str]],
                         user_id: int, profile: str = "",
                         allowed_tags=None, only_transactions: bool = False,
                         source: str = "chat") -> AgentResult:
    """چند متن و/یا چند ویس در یک درخواست (صفِ بعد-از-ساعت‌کاری).

    مثل مسیرِ شرتکات، اینجا هم تاریخچه داده نمی‌شود: محتوای صف خودش کامل است و
    گذاشتنِ خرج‌های قبلیِ همان روز جلوی مدل فقط ریسکِ ثبتِ دوباره‌شان را می‌سازد.
    """
    intro = ("این‌ها خرج‌هایی است که کاربر بعد از پایان سهمیه‌ی قبلی پشت‌سرهم گفته. "
             "همه را با هم و کامل ثبت کن (هر کدام یک یا چند تراکنش).")
    content: list[dict] = [{"type": "text", "text": intro}]
    for t in text_parts:
        if t and t.strip():
            content.append({"type": "text", "text": t.strip()})
    for blob, fmt in audio_items:
        content.append(_audio_part(blob, fmt))
    has_audio = bool(audio_items)
    return await _run_extraction(user_content=content, has_audio=has_audio, user_id=user_id,
                                 history=None, profile=profile, allowed_tags=allowed_tags or [],
                                 only_transactions=only_transactions, source=source)


async def answer_data(user_text: str, history: Optional[list[dict]], user_id: int) -> str:
    """پاسخ به سؤالِ دیتاییِ کاربر با ابزارهای فقط-خواندنی (نقطه‌ی ورودِ عمومی برای روتر)."""
    return await _answer_data(user_text, history, user_id)


async def _answer_data(user_text: str, history: Optional[list[dict]], user_id: int) -> str:
    messages = [{"role": "system", "content": QUERY_SYSTEM.format(today=jalali.today_str())}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_text or "گزارش بده."})

    for _ in range(3):
        msg = await chat(messages, tools=TOOLS_SPEC)
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return (msg.content or "").strip()
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(tc.function.name, args, user_id=user_id)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    return ""
