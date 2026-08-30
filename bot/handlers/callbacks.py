"""هندلر دکمه‌های inline: کارت تراکنش/هدف/بدهی، گزارش، و ویزاردِ دعوت به خانوار."""
from __future__ import annotations

import logging
from io import BytesIO

from telegram import Update
from telegram.ext import ContextTypes

from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY, EXPANDED_KEY
from bot.handlers import cards
from bot.handlers.keyboards import (
    cancel_keyboard,
    detail_scope_keyboard,
    permission_keyboard,
    report_detail_keyboard,
)
from bot.services import clarify as clarify_service
from bot.services import debts as debts_service
from bot.services import household as household_service
from bot.services import reports as reports_service
from bot.services.reports import build_report

logger = logging.getLogger(__name__)

GOAL_EXPANDED_KEY = "expanded_goals"
DEBT_EXPANDED_KEY = "expanded_debts"
INVITE_KEY = "hh_invite"

CANCELLED = "باشه، چیزی تغییر نکرد."


async def start_edit(query, context: ContextTypes.DEFAULT_TYPE, *, kind: str,
                     action: str, obj_id: int, prompt: str) -> None:
    """پرسشِ ویرایش را می‌فرستد و state را نگه می‌دارد.

    شناسه‌ی پیام‌های فرستاده‌شده در `cleanup` جمع می‌شود تا موقع انصراف (یا بعد از
    ویرایشِ موفق) پاک شوند و گفتگو به حالتِ اولش برگردد.
    """
    message = await query.message.reply_text(
        prompt, reply_markup=cancel_keyboard(),
        reply_to_message_id=query.message.message_id,
    )
    context.user_data[AWAITING_KEY] = {
        "kind": kind, "action": action, "id": obj_id,
        "chat_id": message.chat_id, "cleanup": [message.message_id],
    }


async def clear_edit_messages(bot, awaiting: dict) -> None:
    """پیام‌های موقتِ همین ویرایش را پاک می‌کند (پرسش و خطاهای احتمالی)."""
    chat_id = awaiting.get("chat_id")
    for message_id in awaiting.get("cleanup") or []:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:  # noqa: BLE001 — پیامِ قدیمی/پاک‌شده اهمیتی ندارد
            pass


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not user or not household_service.authorized(user.id):
        await query.answer()
        return

    action, _, arg = (query.data or "").partition(":")

    if action == "report":
        await query.answer()
        await query.edit_message_text(build_report(user.id, period=arg),
                                      reply_markup=report_detail_keyboard(arg))
        return

    if action == "rdetail":
        await _on_detail_scope(query, user.id, arg)
        return

    if action == "rdet":
        period, _, scope = arg.partition(":")
        await _send_detail(query, context, user.id, period, scope or reports_service.SCOPE_ALL)
        return

    if action == "clr":
        await _on_clarify(query, context, arg, user.id)
        return

    if action == "editcancel":
        awaiting = context.user_data.pop(AWAITING_KEY, None)
        await query.answer("لغو شد.")
        if awaiting:
            # همه‌چیز به حالتِ قبل از زدنِ دکمه‌ی ویرایش برمی‌گردد: پرسش و خطاها پاک.
            await clear_edit_messages(context.bot, awaiting)
        return

    if action.startswith("hh"):
        await _on_household(query, context, action, arg, user)
        return

    if action.startswith("debt"):
        await _on_debt(query, context, action, int(arg), user.id)
        return

    if action.startswith("goal"):
        await _on_goal(query, context, action, int(arg))
        return

    txn_id = int(arg)
    txn = repo.get_transaction(txn_id)
    if txn is None:
        await query.answer("این تراکنش دیگر وجود ندارد.", show_alert=True)
        return

    if action == "editamt":
        await query.answer()
        await start_edit(query, context, kind="txn", action="amount", obj_id=txn_id,
                         prompt="مبلغ این خرج را بفرست (مثلاً ۲۵۰۰۰۰ یا ۱.۴ برای ارز).")
    elif action == "edittitle":
        await query.answer()
        await start_edit(query, context, kind="txn", action="title", obj_id=txn_id,
                         prompt="عنوان کوتاه این خرج را بنویس.")
    elif action == "details":
        await query.answer()
        expanded = context.user_data.setdefault(EXPANDED_KEY, set())
        expanded.discard(txn_id) if txn_id in expanded else expanded.add(txn_id)
        text, keyboard = cards.render_txn(txn, expanded=txn_id in expanded)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "delete":
        repo.delete_transaction(txn_id)
        await query.answer("حذف شد.")
        await query.edit_message_text("🗑 حذف شد.")
    else:
        await query.answer()


async def _on_goal(query, context: ContextTypes.DEFAULT_TYPE, action: str, goal_id: int) -> None:
    goal = repo.get_goal(goal_id)
    if goal is None:
        await query.answer("این هدف دیگر وجود ندارد.", show_alert=True)
        return

    if action == "goaleditlimit":
        await query.answer()
        await start_edit(query, context, kind="goal", action="limit", obj_id=goal_id,
                         prompt="سقف بودجه را فقط با رقم بفرست (تومان).")
    elif action == "goaledittopic":
        await query.answer()
        await start_edit(query, context, kind="goal", action="topic", obj_id=goal_id,
                         prompt="موضوع این هدف را بنویس (مثلاً رستوران).")
    elif action == "goaldetails":
        await query.answer()
        expanded = context.user_data.setdefault(GOAL_EXPANDED_KEY, set())
        expanded.discard(goal_id) if goal_id in expanded else expanded.add(goal_id)
        text, keyboard = cards.render_goal(goal, expanded=goal_id in expanded)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "goaldelete":
        repo.delete_goal(goal_id)
        await query.answer("حذف شد.")
        await query.edit_message_text("🗑 هدف حذف شد.")
    else:
        await query.answer()


async def _on_debt(query, context: ContextTypes.DEFAULT_TYPE, action: str,
                   debt_id: int, user_id: int) -> None:
    debt = repo.get_debt(debt_id)
    if debt is None:
        await query.answer("این مورد دیگر وجود ندارد.", show_alert=True)
        return

    if action == "debtamt":
        await query.answer()
        await start_edit(query, context, kind="debt", action="amount", obj_id=debt_id,
                         prompt="مبلغ را بفرست (مثلاً ۵۰۰۰۰۰ یا ۱.۴ برای ارز).")
    elif action == "debtparty":
        await query.answer()
        await start_edit(query, context, kind="debt", action="counterparty", obj_id=debt_id,
                         prompt="طرفِ حساب کیست؟ (مثلاً «رضا»)")
    elif action == "debtdetails":
        await query.answer()
        expanded = context.user_data.setdefault(DEBT_EXPANDED_KEY, set())
        expanded.discard(debt_id) if debt_id in expanded else expanded.add(debt_id)
        text, keyboard = cards.render_debt(debt, expanded=debt_id in expanded)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "debtsettle":
        if debt.get("amount") is None:
            await query.answer("اول مبلغ را مشخص کن.", show_alert=True)
            return
        debts_service.settle(user_id, debt_id)
        await query.answer("تسویه شد ✅")
        await cards.refresh_debt_card(context.bot, query.message.chat_id, debt_id)
    elif action == "debtreopen":
        debts_service.reopen(user_id, debt_id)
        await query.answer("به حالت باز برگشت.")
        await cards.refresh_debt_card(context.bot, query.message.chat_id, debt_id)
    elif action == "debtdelete":
        repo.delete_debt(debt_id)
        await query.answer("حذف شد.")
        await query.edit_message_text("🗑 حذف شد.")
    else:
        await query.answer()


# ---------- ویزارد دعوت به خانوار ----------

async def _on_household(query, context: ContextTypes.DEFAULT_TYPE, action: str,
                        arg: str, user) -> None:
    if action == "hhcancel":
        context.user_data.pop(INVITE_KEY, None)
        await query.answer("لغو شد.")
        await query.edit_message_text("باشه، دعوتی ساخته نشد.")
        return

    if action == "hhrel":
        label = household_service.RELATIONS.get(arg, arg)
        context.user_data[INVITE_KEY] = {"relation": arg}
        await query.answer()
        await query.edit_message_text(
            f"نسبت: {label}\n\n"
            "حالا سطح دسترسی این عضو را مشخص کن. ثبت تراکنش، بدهی/طلب و دیدن گزارشِ "
            "خانوار برای هر عضوی باز است؛ تنها چیزی که می‌شود محدودش کرد، هدف‌گذاری است.",
            reply_markup=permission_keyboard(),
        )
        return

    if action == "hhperm":
        state = context.user_data.get(INVITE_KEY) or {}
        relation = state.get("relation")
        if not relation:
            await query.answer()
            await query.edit_message_text(
                "این فرآیند منقضی شده. دوباره «افزودن عضو به خانوار» را بزن.")
            return
        can_set_goals = arg == "1"
        token = household_service.create_invite(user.id, relation, can_set_goals)
        context.user_data.pop(INVITE_KEY, None)
        me = await context.bot.get_me()
        link = household_service.invite_link(me.username, token)
        label = household_service.RELATIONS.get(relation, relation)
        perm_text = "می‌تواند هدف بگذارد" if can_set_goals else "اجازه‌ی هدف‌گذاری ندارد"
        await query.answer()
        await query.edit_message_text(
            "✅ لینک دعوت آماده است:\n\n"
            f"{link}\n\n"
            f"• نسبت: {label}\n"
            f"• دسترسی: {perm_text}\n\n"
            "این لینک را در تلگرام برای همان شخص بفرست. به‌محض اینکه رویش بزند و وارد "
            "ربات شود، با همین دسترسی‌ها عضو خانوارتان می‌شود و از آن لحظه تراکنش‌ها، "
            "بدهی/طلب‌ها و اهدافِ هر دویتان در یک دفترِ مشترک جمع می‌شود.\n"
            "⚠️ این لینک فقط یک‌بار کار می‌کند؛ برای کس دیگری استفاده‌اش نکن.",
            disable_web_page_preview=True,
        )
        return

    await query.answer()


# ---------- ریزِ تراکنش‌ها ----------

async def _on_detail_scope(query, user_id: int, period: str) -> None:
    """در خانوارِ چندنفره می‌پرسیم ریزِ چه کسی؛ کاربرِ تنها مستقیم گزارشش را می‌گیرد."""
    await query.answer()
    if not household_service.is_shared(user_id):
        await _deliver_detail(query, user_id, period, reports_service.SCOPE_ALL)
        return
    members = household_service.members(user_id)
    await query.message.reply_text(
        "ریزِ تراکنش‌های چه کسی را می‌خواهی؟",
        reply_markup=detail_scope_keyboard(period, members, user_id),
    )


async def _send_detail(query, context, user_id: int, period: str, scope: str) -> None:
    await query.answer()
    await _deliver_detail(query, user_id, period, scope)


async def _deliver_detail(query, user_id: int, period: str, scope: str) -> None:
    """پیامِ معمولی، و اگر طولانی بود همان متن به‌صورت فایل .txt.

    گزارشِ ریز عمداً کامل است، پس گاهی از سقفِ پیامِ تلگرام رد می‌شود؛ در آن حالت
    نباید بریده شود — کلِ متن به‌صورت فایل می‌رود.
    """
    text = reports_service.build_detail(user_id, period=period, scope=scope)
    if len(text) <= reports_service.TELEGRAM_SAFE_CHARS:
        await query.message.reply_text(text)
        return
    document = BytesIO(text.encode("utf-8"))
    document.name = reports_service.detail_filename(period, scope)
    await query.message.reply_document(
        document=document,
        filename=document.name,
        caption=f"🧾 ریزِ تراکنش‌ها ({reports_service.scope_label(user_id, scope)}) — "
                "چون طولانی بود، به‌صورت فایل فرستادمش.",
    )


# ---------- ابهامِ تراکنش/بدهی ----------

async def _on_clarify(query, context, arg: str, user_id: int) -> None:
    raw_id, _, choice = arg.partition(":")
    try:
        clar_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    kind, obj_id = clarify_service.resolve(user_id, clar_id, choice)
    if kind is None:
        await query.answer(clarify_service.ALREADY_ANSWERED, show_alert=True)
        return

    await query.answer("ثبت شد ✅")
    if kind == clarify_service.CHOICE_DEBT:
        await query.edit_message_text("🤝 به‌عنوان بدهی/طلب ثبت شد.")
        await cards.send_debt_card(context.bot, query.message.chat_id, obj_id)
    else:
        await query.edit_message_text("💳 به‌عنوان خرج ثبت شد.")
        await cards.send_card(context.bot, query.message.chat_id, obj_id)
