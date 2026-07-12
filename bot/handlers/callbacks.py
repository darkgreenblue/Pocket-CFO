"""هندلر دکمه‌های inline (ویرایش/جزئیات/حذف تراکنش و هدف، و گزارش)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import AWAITING_KEY, EXPANDED_KEY, render_card
from bot.flows.goal_card import render_goal_card
from bot.services.reports import build_report

logger = logging.getLogger(__name__)

GOAL_EXPANDED_KEY = "expanded_goals"


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not user or not settings.is_authorized(user.id):
        await query.answer()
        return

    action, _, arg = (query.data or "").partition(":")

    if action == "report":
        await query.answer()
        await query.edit_message_text(build_report(user.id, period=arg))
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
        text, keyboard = render_card(txn, expanded=txn_id in expanded)
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
        text, keyboard = render_goal_card(goal, expanded=goal_id in expanded)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "goaldelete":
        repo.delete_goal(goal_id)
        await query.answer("حذف شد.")
        await query.edit_message_text("🗑 هدف حذف شد.")
    else:
        await query.answer()
