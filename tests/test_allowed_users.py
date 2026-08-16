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


def _settings_with(env_value, access_mode=None):
    """config را با ALLOWED_USER_IDS (و در صورت نیاز ACCESS_MODE) دوباره بارگذاری می‌کند."""
    from bot import config
    saved = {k: os.environ.get(k) for k in ("ALLOWED_USER_IDS", "ACCESS_MODE")}
    os.environ["ALLOWED_USER_IDS"] = env_value
    if access_mode is not None:
        os.environ["ACCESS_MODE"] = access_mode
    try:
        return importlib.reload(config).load_settings()
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
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


def test_open_mode_lets_everyone_in_regardless_of_the_list():
    """روزِ «باز برای همه»: فقط ACCESS_MODE عوض می‌شود و لیست دست‌نخورده می‌ماند."""
    from bot.config import ACCESS_OPEN
    settings = _settings_with(str(OWNER), access_mode=ACCESS_OPEN)
    assert settings.is_authorized(999999)
    assert settings.is_authorized(OWNER)
    # لیست پاک نشده؛ برگرداندنِ محدودیت هم یک خط است.
    assert OWNER in settings.allowed_user_ids


def test_default_mode_is_still_restricted():
    from bot.config import ACCESS_ALLOWLIST
    settings = _settings_with(str(OWNER))
    assert settings.access_mode == ACCESS_ALLOWLIST
    assert not settings.is_authorized(999999)


def test_access_summary_reports_the_effective_state():
    """این خط در لاگِ راه‌اندازی می‌آید؛ باید حقیقت را بگوید، نه نیت را."""
    from bot.config import ACCESS_OPEN
    assert "باز برای همه" in _settings_with(str(OWNER), access_mode=ACCESS_OPEN).access_summary()
    assert "عملاً باز" in _settings_with("").access_summary()
    restricted = _settings_with(str(OWNER)).access_summary()
    assert "محدود به" in restricted and str(EXTRA) in restricted
