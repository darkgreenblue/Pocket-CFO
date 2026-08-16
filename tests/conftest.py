"""پیکربندی تست‌ها: env پیش‌فرض و دیتابیس موقت تا importِ config کار کند."""
import os
import tempfile

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("ALLOWED_USER_IDS", "1")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "cfo_pytest.db"))

import pytest  # noqa: E402


@pytest.fixture
def db():
    from bot.config import settings
    from bot.db import repo
    if os.path.exists(settings.db_path):
        os.remove(settings.db_path)
    repo.init_db()
    return repo


@pytest.fixture
def set_setting():
    """تغییر موقتِ یک فیلد از `settings` (که frozen است، پس monkeypatch کار نمی‌کند).

    روی **همه‌ی** نمونه‌های زنده‌ی Settings اعمال می‌شود، نه فقط `bot.config.settings`:
    `test_allowed_users` ماژولِ config را reload می‌کند و یک نمونه‌ی تازه می‌سازد، ولی
    ماژول‌هایی که قبلاً `from bot.config import settings` کرده‌اند هنوز نمونه‌ی قبلی را
    نگه داشته‌اند. اگر فقط یکی را عوض کنیم، کدِ زیرِ تست نسخه‌ی دیگری را می‌بیند.
    """
    import sys

    def _live_instances() -> list[object]:
        # با نامِ کلاس تطبیق می‌دهیم نه isinstance: reload کلاسِ Settings را هم از نو
        # می‌سازد، پس نمونه‌های قدیمی دیگر instanceِ کلاسِ فعلی نیستند.
        found: dict[int, object] = {}
        for module in list(sys.modules.values()):
            candidate = getattr(module, "settings", None)
            if type(candidate).__name__ == "Settings":
                found[id(candidate)] = candidate
        return list(found.values())

    originals: list[tuple[object, str, object]] = []

    def _set(name: str, value: object) -> None:
        for instance in _live_instances():
            originals.append((instance, name, getattr(instance, name)))
            object.__setattr__(instance, name, value)

    yield _set
    for instance, name, value in reversed(originals):
        object.__setattr__(instance, name, value)
