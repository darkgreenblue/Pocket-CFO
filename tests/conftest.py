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
    """تغییر موقتِ یک فیلد از `settings` (که frozen است، پس monkeypatch کار نمی‌کند)."""
    from bot.config import settings
    originals: list[tuple[str, object]] = []

    def _set(name: str, value: object) -> None:
        originals.append((name, getattr(settings, name)))
        object.__setattr__(settings, name, value)

    yield _set
    for name, value in reversed(originals):
        object.__setattr__(settings, name, value)
