"""دستورهای ربات: /start (آنبوردینگ + پذیرشِ لینکِ دعوت)، /report، /household و /shortcut."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.handlers.keyboards import BTN_HOUSEHOLD, main_menu, report_period_keyboard
from bot.services import household as household_service
from bot.services import ingest as ingest_service

logger = logging.getLogger(__name__)

WELCOME = (
    "سلام! من «CFO جیبی» تو هستم 👔\n"
    "دستیار مالی‌ای که کمک می‌کنه خرج‌های روزمره‌ت رو راحت ثبت کنی.\n\n"
    "چطوری کار می‌کنم؟\n"
    "• کافیه یک ویس بگیری یا یک متن بفرستی و بگی چی خریدی.\n"
    "• من از حرفت تراکنش‌ها رو درمیارم و برای هرکدوم یه کارت تأیید می‌فرستم.\n"
    "• اگه از قرض و بدهی حرف بزنی («۵۰۰ به رضا قرض دادم»)، به‌جای خرج، یه کارتِ "
    "بدهی/طلب می‌سازم.\n"
    "• اگه سقف بذاری («این ماه بیشتر از ۳ میلیون رستوران نرم»)، کارتِ هدف می‌سازم و "
    "سرِ ۵۰٪ و ۸۰٪ و ۱۰۰٪ خبرت می‌کنم.\n"
    "• مبلغ و عنوان حتماً باید تأیید بشن؛ بقیه‌ی توضیحات اختیاریه.\n\n"
    "یک نکته‌ی مهم: هرچی موقع ثبت توضیح بیشتری بدی (مثلاً برای چی، کجا، چه اقلامی)، "
    "گزارش‌ها و تحلیل‌های مالی آینده‌ت دقیق‌تر می‌شه. 🙂\n\n"
    f"اگه مالیِ مشترک دارید، با «{BTN_HOUSEHOLD}» می‌تونی پارتنرت رو هم اضافه کنی.\n\n"
    "همین الان امتحان کن: یه ویس بگیر و بگو امروز چی خریدی."
)

NOT_ALLOWED = "این ربات شخصی است و برای تو فعال نیست."


def _full_name(user) -> str:
    return (getattr(user, "full_name", None) or user.first_name or "").strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    payload = context.args[0] if context.args else ""
    token = household_service.parse_invite_payload(payload)

    # لینک دعوت پیش از بررسیِ دسترسی پردازش می‌شود؛ خودِ لینک مجوزِ ورود است.
    if token:
        await _accept_invite(update, token, user)
        return

    if user and not household_service.authorized(user.id):
        await update.message.reply_text(NOT_ALLOWED)
        return
    household_service.touch(user.id, _full_name(user))
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def _accept_invite(update: Update, token: str, user) -> None:
    try:
        household_service.accept_invite(token, user.id, _full_name(user))
    except household_service.JoinError as exc:
        await update.message.reply_text(str(exc))
        return
    except Exception:  # noqa: BLE001
        logger.exception("پذیرشِ دعوتِ خانوار ناموفق بود")
        await update.message.reply_text("پیوستن به خانوار الان ممکن نشد. دوباره تلاش کن.")
        return

    names = [m["display_name"] or household_service.DEFAULT_NAME
             for m in household_service.members(user.id) if m["user_id"] != user.id]
    partner = names[0] if names else "عضو دیگر"
    permission_line = ("می‌تونی برای خانوار هدف/سقف بودجه هم تعیین کنی."
                       if household_service.can_set_goals(user.id)
                       else "هدف‌گذاری برای اکانتِ تو فعال نیست؛ بقیه‌ی کارها باز است.")
    await update.message.reply_text(
        "✅ به خانوار اضافه شدی!\n\n"
        f"از این به بعد هر خرج، بدهی/طلب یا هدفی که تو یا {partner} ثبت کنید، در یک دفترِ "
        "مشترک جمع می‌شود — با این تفاوت که همیشه معلوم است هرکدام را چه کسی ثبت کرده.\n"
        "هشدارهای بودجه هم برای هر دویتان فرستاده می‌شود.\n"
        f"{permission_line}\n\n"
        "همین حالا امتحان کن: یه ویس بگیر و بگو امروز چی خریدی.",
        reply_markup=main_menu(),
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and not household_service.authorized(user.id):
        return
    await update.message.reply_text("کدوم بازه؟", reply_markup=report_period_keyboard())


async def cmd_household(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وضعیت خانوار: چه کسانی عضوند و هرکدام چه دسترسی‌ای دارند."""
    user = update.effective_user
    if user and not household_service.authorized(user.id):
        return
    household_service.touch(user.id, _full_name(user))
    members = household_service.members(user.id)
    lines = ["👨‍👩‍👧 خانوار تو:"]
    for member in members:
        name = (member["display_name"] or "").strip() or household_service.DEFAULT_NAME
        role = "سازنده" if member["role"] == "owner" else (member["relation"] or "عضو")
        goal_flag = "هدف‌گذاری ✅" if member["can_set_goals"] else "هدف‌گذاری 🚫"
        me = " (تو)" if member["user_id"] == user.id else ""
        lines.append(f"• {name}{me} — {role} — {goal_flag}")
    if len(members) == 1:
        lines.append("")
        lines.append(f"فعلاً تنهایی. با دکمه‌ی «{BTN_HOUSEHOLD}» می‌تونی پارتنرت رو دعوت کنی.")
    await update.message.reply_text("\n".join(lines))


SHORTCUT_DISABLED = (
    "میان‌برِ گوشی روی این سرور فعال نیست. (ادمین باید `INGEST_PUBLIC_URL` را در "
    "`.env` بگذارد.)"
)

SHORTCUT_GUIDE = """📱 ثبتِ خرج با سه ضربه پشتِ گوشی

این آدرسِ شخصیِ توست. **مثل رمز باهاش رفتار کن** — هرکس داشته باشد می‌تواند روی دفترِ مالیِ تو خرج ثبت کند:

`{url}`

**۱) میان‌بر را بساز** — در اپ Shortcuts یک میان‌برِ تازه با همین سه اکشن:

• `Dictate Text` — زبان: Persian
• `Get Contents of URL` — آدرسِ بالا، Method روی `POST`، و در Request Body گزینه‌ی `File` را بزن و متغیرِ `Dictated Text` را بگذار
• `Show Notification` — متنِ پاسخ

**۲) وصلش کن به پشتِ گوشی**
Settings › Accessibility › Touch › Back Tap › Triple Tap → همین میان‌بر

**۳) تمام.** سه ضربه پشتِ گوشی → حرف بزن → کارتش همین‌جا برایت می‌آید.

🎙 اگر ویس می‌خواهی به‌جای دیکته: به‌جای `Dictate Text` از `Record Audio` استفاده کن و به آخرِ آدرس `?audio=m4a` اضافه کن.

⚠️ از این مسیر فقط **خرج** ثبت می‌شود. بدهی، هدف و گزارش را همین‌جا در تلگرام بگو.

اگر آدرس لو رفت، دوباره /shortcut بزن — آدرسِ قبلی همان لحظه باطل می‌شود."""


async def cmd_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """توکنِ شخصیِ کاربر را صادر می‌کند و راهنمای میان‌بر را می‌دهد.

    نقطه‌ی اسکیل‌پذیریِ راه‌اندازی: هر کاربرِ تازه خودش توکنش را از اینجا می‌گیرد،
    بدونِ اینکه کسی `.env` را ویرایش یا سرور را ری‌استارت کند.
    """
    user = update.effective_user
    if user and not household_service.authorized(user.id):
        await update.message.reply_text(NOT_ALLOWED)
        return
    household_service.touch(user.id, _full_name(user))

    if not settings.ingest_public_url:
        await update.message.reply_text(SHORTCUT_DISABLED, parse_mode="Markdown")
        return

    token = ingest_service.issue_token(user.id)
    url = f"{settings.ingest_public_url}/s/{token}"
    await update.message.reply_text(SHORTCUT_GUIDE.format(url=url), parse_mode="Markdown")
