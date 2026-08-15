"""هندلر دکمه‌های inline: کارت تراکنش/هدف/بدهی، گزارش، و ویزاردِ دعوت به خانوار."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY, EXPANDED_KEY
from bot.handlers import cards
from bot.handlers.keyboards import permission_keyboard
from bot.services import debts as debts_service
from bot.services import household as household_service
from bot.services.reports import build_report

logger = logging.getLogger(__name__)

GOAL_EXPANDED_KEY = "expanded_goals"
DEBT_EXPANDED_KEY = "expanded_debts"
INVITE_KEY = "hh_invite"


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not user or not household_service.authorized(user.id):
        await query.answer()
        return

    action, _, arg = (query.data or "").partition(":")

    if action == "report":
        await query.answer()
        await query.edit_message_text(build_report(user.id, period=arg))
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
        context.user_data[AWAITING_KEY] = {"kind": "txn", "action": "amount", "id": txn_id}
        await query.message.reply_text("مبلغ این خرج را فقط با رقم بفرست (مثلاً ۲۵۰۰۰۰).",
                                       reply_to_message_id=query.message.message_id)
    elif action == "edittitle":
        await query.answer()
        context.user_data[AWAITING_KEY] = {"kind": "txn", "action": "title", "id": txn_id}
        await query.message.reply_text("عنوان کوتاه این خرج را بنویس.",
                                       reply_to_message_id=query.message.message_id)
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
        context.user_data[AWAITING_KEY] = {"kind": "goal", "action": "limit", "id": goal_id}
        await query.message.reply_text("سقف بودجه را فقط با رقم بفرست (تومان).",
                                       reply_to_message_id=query.message.message_id)
    elif action == "goaledittopic":
        await query.answer()
        context.user_data[AWAITING_KEY] = {"kind": "goal", "action": "topic", "id": goal_id}
        await query.message.reply_text("موضوع این هدف را بنویس (مثلاً رستوران).",
                                       reply_to_message_id=query.message.message_id)
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
        context.user_data[AWAITING_KEY] = {"kind": "debt", "action": "amount", "id": debt_id}
        await query.message.reply_text("مبلغ را فقط با رقم بفرست (مثلاً ۵۰۰۰۰۰).",
                                       reply_to_message_id=query.message.message_id)
    elif action == "debtparty":
        await query.answer()
        context.user_data[AWAITING_KEY] = {"kind": "debt", "action": "counterparty", "id": debt_id}
        await query.message.reply_text("طرفِ حساب کیست؟ (مثلاً «رضا»)",
                                       reply_to_message_id=query.message.message_id)
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
