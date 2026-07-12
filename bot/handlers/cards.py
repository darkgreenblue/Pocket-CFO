"""ارسال و به‌روزرسانی کارت تراکنش (مشترک بین هندلرها و صفِ صبح)."""
from __future__ import annotations

import logging

from bot.db import repo
from bot.flows.draft_flow import render_card
from bot.flows.goal_card import render_goal_card

logger = logging.getLogger(__name__)


async def send_card(bot, chat_id: int, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_card(txn)
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    repo.set_card_message(txn_id, chat_id, msg.message_id)


async def refresh_card(bot, chat_id: int, txn_id: int, expanded: bool = False) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_card(txn, expanded=expanded)
    if txn.get("card_message_id") and txn.get("card_chat_id"):
        try:
            await bot.edit_message_text(
                text=text, chat_id=txn["card_chat_id"],
                message_id=txn["card_message_id"], reply_markup=keyboard,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    await send_card(bot, chat_id, txn_id)


async def send_goal_card(bot, chat_id: int, goal_id: int) -> None:
    goal = repo.get_goal(goal_id)
    if goal is None:
        return
    text, keyboard = render_goal_card(goal)
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    repo.set_goal_card(goal_id, chat_id, msg.message_id)


async def refresh_goal_card(bot, chat_id: int, goal_id: int, expanded: bool = False) -> None:
    goal = repo.get_goal(goal_id)
    if goal is None:
        return
    text, keyboard = render_goal_card(goal, expanded=expanded)
    if goal.get("card_message_id") and goal.get("card_chat_id"):
        try:
            await bot.edit_message_text(
                text=text, chat_id=goal["card_chat_id"],
                message_id=goal["card_message_id"], reply_markup=keyboard,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    await send_goal_card(bot, chat_id, goal_id)
