"""هندلر دکمه‌های inline (تأیید/ویرایش/واحد/جزئیات/حذف و گزارش)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.db import repo
from bot.flows.draft_flow import (
    AWAITING_KEY,
    EXPANDED_KEY,
    confirmed_text,
    render_card,
)
from bot.services.reports import build_report
from bot.services.transactions import is_complete
from bot.utils.money import other_unit

logger = logging.getLogger(__name__)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not user or not settings.is_authorized(user.id):
        await query.answer()
        return

    await query.answer()
    action, _, arg = query.data.partition(":")

    if action == "report":
        await query.edit_message_text(build_report(user.id, period=arg))
        return

    txn_id = int(arg)
    txn = repo.get_transaction(txn_id)
    if txn is None:
        await query.edit_message_text("این تراکنش دیگر وجود ندارد.")
        return

    if action == "confirm":
        await _confirm(query, txn_id)
    elif action == "editamt":
        context.user_data[AWAITING_KEY] = {"action": "amount", "txn_id": txn_id}
        await query.message.reply_text("مبلغ را فقط به‌صورت رقم بفرست (مثلاً ۲۵۰۰۰۰).")
    elif action == "edittitle":
        context.user_data[AWAITING_KEY] = {"action": "title", "txn_id": txn_id}
        await query.message.reply_text("عنوان کوتاه این خرج را بنویس.")
    elif action == "unit":
        repo.update_transaction(txn_id, currency_display=other_unit(txn.get("currency_display", "toman")))
        await _refresh(query, context, txn_id)
    elif action == "details":
        expanded = context.user_data.setdefault(EXPANDED_KEY, set())
        if txn_id in expanded:
            expanded.discard(txn_id)
        else:
            expanded.add(txn_id)
        await _refresh(query, context, txn_id)
    elif action == "delete":
        repo.delete_transaction(txn_id)
        await query.edit_message_text("🗑 حذف شد.")


async def _confirm(query, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    if not is_complete(txn):
        missing = "عنوان" if not txn.get("title") else "مبلغ"
        await query.message.reply_text(f"قبل از ثبت، «{missing}» را با دکمه‌ی ویرایش کامل کن.")
        return
    repo.confirm_transaction(txn_id)
    txn = repo.get_transaction(txn_id)
    await query.edit_message_text(confirmed_text(txn))


async def _refresh(query, context: ContextTypes.DEFAULT_TYPE, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    expanded = txn_id in context.user_data.get(EXPANDED_KEY, set())
    text, keyboard = render_card(txn, expanded=expanded)
    await query.edit_message_text(text, reply_markup=keyboard)
