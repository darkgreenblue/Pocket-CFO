"""ارتقا از اسکیمای قبل از «خانوار» روی یک دیتابیسِ پُر.

این تست به‌خاطر یک باگِ واقعیِ پروداکشن نوشته شد: ایندکسِ `household_id` داخل
`schema.sql` بود و چون آن فایل **قبل از** مهاجرت اجرا می‌شود، روی دیتابیسِ موجود
(که ستون را هنوز نداشت) با `no such column: household_id` می‌ترکید و ربات اصلاً بالا
نمی‌آمد. بقیه‌ی تست‌ها همیشه از دیتابیسِ خالی شروع می‌کردند و این مسیر را نمی‌دیدند.
"""
import os
import sqlite3
from datetime import datetime

import pytest

USER = 555

# اسکیمای دقیقِ نسخه‌ی قبل از فیچرِ خانوار (بدون household_id و بدون جدول‌های خانوار/بدهی)
OLD_SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES tags(id), level INTEGER NOT NULL DEFAULT 1,
    aliases TEXT NOT NULL DEFAULT '[]');
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', title TEXT, amount INTEGER,
    currency_display TEXT NOT NULL DEFAULT 'toman', note TEXT,
    mentioned_items TEXT NOT NULL DEFAULT '[]',
    needs_later_completion INTEGER NOT NULL DEFAULT 0,
    reminded INTEGER NOT NULL DEFAULT 0, transcript TEXT, source TEXT,
    card_chat_id INTEGER, card_message_id INTEGER, jyear INTEGER, jmonth INTEGER,
    created_at TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE transaction_tags (
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id), PRIMARY KEY (transaction_id, tag_id));
CREATE TABLE tag_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, transaction_id INTEGER,
    name TEXT NOT NULL, created_at TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, weight INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', topic TEXT, tag_id INTEGER REFERENCES tags(id),
    limit_amount INTEGER, jyear INTEGER NOT NULL, jmonth INTEGER NOT NULL, note TEXT,
    last_alert_level INTEGER NOT NULL DEFAULT 0, card_chat_id INTEGER,
    card_message_id INTEGER, created_at TEXT NOT NULL);
CREATE TABLE user_profile (
    user_id INTEGER PRIMARY KEY, profile TEXT NOT NULL DEFAULT '', updated_at TEXT);
CREATE TABLE pending_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text', content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX idx_txn_user_status ON transactions(user_id, status);
CREATE INDEX idx_txn_created ON transactions(created_at);
CREATE INDEX idx_msg_user ON messages(user_id, id);
"""


@pytest.fixture
def legacy_db():
    """دیتابیسی با اسکیمای قدیمی و چند رکوردِ واقعی — آماده‌ی ارتقا."""
    from bot.config import settings
    if os.path.exists(settings.db_path):
        os.remove(settings.db_path)
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(OLD_SCHEMA)
    now = datetime.now().isoformat()
    conn.execute("INSERT INTO tags(name, parent_id, level, aliases) "
                 "VALUES ('رستوران', NULL, 1, '[]')")
    for i in range(3):
        conn.execute(
            "INSERT INTO transactions(user_id, status, title, amount, currency_display, note,"
            " mentioned_items, needs_later_completion, reminded, transcript, source,"
            " jyear, jmonth, created_at, confirmed_at)"
            " VALUES (?, 'confirmed', ?, ?, 'toman', '', '[]', 0, 0, '', 'chat',"
            " 1405, 5, ?, ?)",
            (USER, f"خرج {i}", 100000 * (i + 1), now, now),
        )
    conn.execute("INSERT INTO transaction_tags(transaction_id, tag_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO goals(user_id, status, topic, tag_id, limit_amount, jyear, jmonth,"
        " note, last_alert_level, created_at)"
        " VALUES (?, 'active', 'رستوران', 1, 3000000, 1405, 5, '', 0, ?)",
        (USER, now),
    )
    conn.execute("INSERT INTO user_profile(user_id, profile, updated_at) VALUES (?, ?, ?)",
                 (USER, "کاربر قدیمی", now))
    conn.commit()
    conn.close()

    from bot.db import repo
    return repo


def test_upgrade_from_pre_household_schema_does_not_crash(legacy_db):
    """این همان کالی است که در بوتِ ربات اجرا می‌شود؛ خطا یعنی ربات بالا نمی‌آید."""
    legacy_db.init_db()
    legacy_db.init_db()   # دوباره اجرا شود (هر ری‌استارت) — باید idempotent باشد


def test_upgrade_preserves_data_and_backfills_household(legacy_db):
    legacy_db.init_db()

    household_id = legacy_db.find_household_id(USER)
    assert household_id is not None

    txns = legacy_db.list_user_transactions(USER)
    assert len(txns) == 3
    assert all(t["household_id"] == household_id for t in txns)

    goals = legacy_db.active_goals_for_month(USER, 1405, 5)
    assert len(goals) == 1 and goals[0]["household_id"] == household_id

    assert legacy_db.get_profile(USER) == "کاربر قدیمی"


def test_upgraded_db_supports_the_new_features(legacy_db):
    """بعد از ارتقا، بدهی و خانوار باید بدون مشکل کار کنند."""
    legacy_db.init_db()
    from bot.services import debts, household as hh

    debt_id, created = debts.create_or_update_from_item(
        USER, {"kind": "debt", "counterparty": "رضا", "amount": 500000})
    assert created and legacy_db.get_debt(debt_id)["status"] == "open"

    token = hh.create_invite(USER, "partner", True)
    hh.accept_invite(token, 556, "پارتنر")
    assert hh.is_shared(USER) and len(legacy_db.list_debts(556)) == 1
