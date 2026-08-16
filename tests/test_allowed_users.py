"""ترکیبِ آی‌دی‌های مجازِ .env با آی‌دی‌های ثابتِ کد.

نکته‌ی حساس: لیستِ خالی در .env یعنی «ربات برای همه باز است». اگر آی‌دی‌های کد را در آن
حالت هم اضافه کنیم، لیست غیرخالی می‌شود و ربات ناگهان فقط به همان‌ها جواب می‌دهد —
یعنی صاحبِ ربات از ربات خودش بیرون می‌افتد. این تست جلوی آن رگرسیون را می‌گیرد.
"""
import importlib
import os

import pytest

EXTRA = 429557996
OWNER = 111


def _settings_with(env_value):
    """config را با یک ALLOWED_USER_IDS مشخص دوباره بارگذاری می‌کند."""
    from bot import config
    old = os.environ.get("ALLOWED_USER_IDS")
    os.environ["ALLOWED_USER_IDS"] = env_value
    try:
        return importlib.reload(config).load_settings()
    finally:
        if old is None:
            os.environ.pop("ALLOWED_USER_IDS", None)
        else:
            os.environ["ALLOWED_USER_IDS"] = old
        importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    import bot.config
    importlib.reload(bot.config)


def test_extra_id_is_authorized_alongside_env_ids():
    settings = _settings_with(str(OWNER))
    assert settings.is_authorized(EXTRA)
    assert settings.is_authorized(OWNER)
    assert not settings.is_authorized(999999)


def test_extra_ids_survive_multiple_env_ids():
    settings = _settings_with(f"{OWNER}, 222")
    for uid in (OWNER, 222, EXTRA):
        assert settings.is_authorized(uid), uid


def test_empty_env_list_stays_open_to_everyone():
    """لیستِ خالی نباید با آی‌دی‌های کد ناگهان محدود شود."""
    settings = _settings_with("")
    assert settings.allowed_user_ids == frozenset()
    assert settings.is_authorized(EXTRA)
    assert settings.is_authorized(123456789)     # هر کسِ دیگر هم همچنان مجاز است
