"""ارسال و به‌روزرسانی کارت‌ها (تراکنش، هدف، بدهی/طلب) — مشترک بین هندلرها و صفِ صبح.

سه نوع کارت داریم و هر سه یک رفتار دارند: ساخت پیام، ذخیره‌ی محلِ کارت، و ویرایشِ
همان پیام هنگام تغییر. در خانوارِ چندنفره، نامِ ثبت‌کننده هم روی کارت می‌آید.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from bot.db import repo
from bot.flows.debt_card import render_debt_card
from bot.flows.draft_flow import render_card
from bot.flows.goal_card import render_goal_card
from bot.services import household as household_service

logger = logging.getLogger(__name__)


def recorder_name(row: dict[str, Any]) -> Optional[str]:
    """نامِ ثبت‌کننده — فقط وقتی خانوار بیش از یک عضو دارد (وگرنه نویزِ اضافه است)."""
    user_id = row.get("user_id")
    if user_id is None or not household_service.is_shared(user_id):
        return None
    return household_service.display_name(user_id)


def render_txn(txn: dict[str, Any], *, expanded: bool = False):
    return render_card(txn, expanded=expanded, recorder_name=recorder_name(txn))


def render_goal(goal: dict[str, Any], *, expanded: bool = False):
    return render_goal_card(goal, expanded=expanded, recorder_name=recorder_name(goal))


def render_debt(debt: dict[str, Any], *, expanded: bool = False):
    return render_debt_card(debt, expanded=expanded, recorder_name=recorder_name(debt))


async def _send(bot, chat_id: int, text: str, keyboard):
    return await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


async def _edit_or_send(bot, chat_id: int, row: dict[str, Any], text: str, keyboard,
                        send_again) -> None:
    if row.get("card_message_id") and row.get("card_chat_id"):
        try:
            await bot.edit_message_text(
                text=text, chat_id=row["card_chat_id"],
                message_id=row["card_message_id"], reply_markup=keyboard,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    await send_again()


# ---------- تراکنش ----------

async def send_card(bot, chat_id: int, txn_id: int) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_txn(txn)
    msg = await _send(bot, chat_id, text, keyboard)
    repo.set_card_message(txn_id, chat_id, msg.message_id)


async def refresh_card(bot, chat_id: int, txn_id: int, expanded: bool = False) -> None:
    txn = repo.get_transaction(txn_id)
    if txn is None:
        return
    text, keyboard = render_txn(txn, expanded=expanded)
    await _edit_or_send(bot, chat_id, txn, text, keyboard,
                        lambda: send_card(bot, chat_id, txn_id))


# ---------- هدف ----------

async def send_goal_card(bot, chat_id: int, goal_id: int) -> None:
    goal = repo.get_goal(goal_id)
    if goal is None:
        return
    text, keyboard = render_goal(goal)
    msg = await _send(bot, chat_id, text, keyboard)
    repo.set_goal_card(goal_id, chat_id, msg.message_id)


async def refresh_goal_card(bot, chat_id: int, goal_id: int, expanded: bool = False) -> None:
    goal = repo.get_goal(goal_id)
    if goal is None:
        return
    text, keyboard = render_goal(goal, expanded=expanded)
    await _edit_or_send(bot, chat_id, goal, text, keyboard,
                        lambda: send_goal_card(bot, chat_id, goal_id))


# ---------- بدهی و طلب ----------

async def send_debt_card(bot, chat_id: int, debt_id: int) -> None:
    debt = repo.get_debt(debt_id)
    if debt is None:
        return
    text, keyboard = render_debt(debt)
    msg = await _send(bot, chat_id, text, keyboard)
    repo.set_debt_card(debt_id, chat_id, msg.message_id)


async def refresh_debt_card(bot, chat_id: int, debt_id: int, expanded: bool = False) -> None:
    debt = repo.get_debt(debt_id)
    if debt is None:
        return
    text, keyboard = render_debt(debt, expanded=expanded)
    await _edit_or_send(bot, chat_id, debt, text, keyboard,
                        lambda: send_debt_card(bot, chat_id, debt_id))
