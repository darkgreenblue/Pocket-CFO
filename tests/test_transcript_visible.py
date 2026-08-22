"""«چیزی که ربات شنید» باید روی کارت دیده شود.

رونویسیِ هر ثبتِ صوتی از اول در دیتابیس ذخیره می‌شد ولی هیچ‌جا نمایش داده نمی‌شد. برای
همین وقتی ثبت‌های میان‌بر عنوانشان «نامشخص» درمی‌آمد، هیچ راهی نبود بفهمیم مشکل از
شنیدن است یا از استخراج — مگر با SSH زدن به دیتابیسِ سرور.
"""
from bot.flows.draft_flow import TRANSCRIPT_MAX, render_card
from bot.services import transactions as ts

USER = 1


def _buttons(keyboard):
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


def test_transcript_shows_in_details(db):
    txn_id = ts.create_from_item(USER, {"title": "قهوه", "amount": 120000},
                                 transcript="صد و بیست تومن قهوه گرفتم")
    txn = db.get_transaction(txn_id)

    collapsed, _ = render_card(txn)
    assert "شنیدم" not in collapsed          # کارتِ جمع‌شده شلوغ نمی‌شود

    expanded, _ = render_card(txn, expanded=True)
    assert "🎙 شنیدم: «صد و بیست تومن قهوه گرفتم»" in expanded


def test_details_button_appears_for_a_bare_voice_transaction(db):
    """ثبتِ صوتیِ بی‌تگ و بی‌آیتم هم باید دکمه‌ی جزئیات داشته باشد — همان‌جا که لازم است."""
    txn_id = ts.create_from_item(USER, {"amount": 150000}, transcript="صد و پنجاه تومن")
    txn = db.get_transaction(txn_id)

    _, keyboard = render_card(txn)
    assert f"details:{txn_id}" in _buttons(keyboard)


def test_untitled_shortcut_transaction_shows_what_was_heard(db):
    """سناریوی گزارش‌شده: عنوان «نامشخص» — رونویسی باید بگوید چرا."""
    txn_id = ts.create_from_item(USER, {"amount": 150000},
                                 transcript="صد و پنجاه تومن", source="shortcut")
    expanded, _ = render_card(db.get_transaction(txn_id), expanded=True)

    assert "نیازمند تکمیل: عنوان" in expanded
    assert "صد و پنجاه تومن" in expanded


def test_long_transcript_is_truncated(db):
    long_text = "خرید " * 200
    txn_id = ts.create_from_item(USER, {"title": "خرید", "amount": 1000},
                                 transcript=long_text)
    expanded, _ = render_card(db.get_transaction(txn_id), expanded=True)

    heard = [ln for ln in expanded.splitlines() if ln.startswith("🎙")][0]
    assert heard.endswith("…»") or heard.endswith("…") or "…" in heard
    assert len(heard) < TRANSCRIPT_MAX + 40


def test_no_transcript_line_when_there_is_none(db):
    txn_id = ts.create_from_item(USER, {"title": "نان", "amount": 200000})
    expanded, _ = render_card(db.get_transaction(txn_id), expanded=True)
    assert "شنیدم" not in expanded
