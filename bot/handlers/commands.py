"""دستورهای ربات: /start (آنبوردینگ) و /report."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.handlers.keyboards import main_menu, report_period_keyboard

WELCOME = (
    "سلام! من «CFO جیبی» تو هستم 👔\n"
    "دستیار مالی‌ای که کمک می‌کنه خرج‌های روزمره‌ت رو راحت ثبت کنی.\n\n"
    "چطوری کار می‌کنم؟\n"
    "• کافیه یک ویس بگیری یا یک متن بفرستی و بگی چی خریدی.\n"
    "• من از حرفت تراکنش‌ها رو درمیارم و برای هرکدوم یه کارت تأیید می‌فرستم.\n"
    "• مبلغ و عنوان حتماً باید تأیید بشن؛ بقیه‌ی توضیحات اختیاریه.\n\n"
    "یک نکته‌ی مهم: هرچی موقع ثبت توضیح بیشتری بدی (مثلاً برای چی، کجا، چه اقلامی)، "
    "گزارش‌ها و تحلیل‌های مالی آینده‌ت دقیق‌تر می‌شه. 🙂\n\n"
    "همین الان امتحان کن: یه ویس بگیر و بگو امروز چی خریدی."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and not settings.is_authorized(user.id):
        await update.message.reply_text("این ربات شخصی است و برای تو فعال نیست.")
        return
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and not settings.is_authorized(user.id):
        return
    await update.message.reply_text("کدوم بازه؟", reply_markup=report_period_keyboard())
