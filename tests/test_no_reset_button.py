"""نگهبانِ حذفِ همیشگیِ «ریست تست».

دکمه‌ی موقتِ ریست بعد از پایانِ تست‌ها و شروعِ استفاده‌ی واقعیِ خانوار حذف شد. حالا که
دفتر مشترک است، یک لمسِ اشتباهی می‌توانست دیتای هر دو عضو را ببرد. این تست هست تا آن
مسیر ناخواسته برنگردد.
"""


def test_main_menu_has_no_reset_button():
    from bot.handlers import keyboards
    labels = [button.text for row in keyboards.main_menu().keyboard for button in row]
    assert not any("ریست" in label or "reset" in label.lower() for label in labels), labels
    assert not hasattr(keyboards, "BTN_RESET")


def test_repo_exposes_no_bulk_wipe_helper():
    from bot.db import repo
    assert not hasattr(repo, "reset_user")
