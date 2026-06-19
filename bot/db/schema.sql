CREATE TABLE IF NOT EXISTS tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES tags(id),
    level     INTEGER NOT NULL DEFAULT 1,
    aliases   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS transactions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'draft',   -- draft | confirmed
    title                  TEXT,
    amount                 INTEGER,                          -- عدد خام در واحد currency_display
    currency_display       TEXT NOT NULL DEFAULT 'toman',
    note                   TEXT,
    mentioned_items        TEXT NOT NULL DEFAULT '[]',       -- JSON: اقلام سبد (متادیتا)
    needs_later_completion INTEGER NOT NULL DEFAULT 0,
    reminded               INTEGER NOT NULL DEFAULT 0,
    transcript             TEXT,
    source                 TEXT,                             -- voice | text
    card_chat_id           INTEGER,
    card_message_id        INTEGER,
    created_at             TEXT NOT NULL,
    confirmed_at           TEXT
);

CREATE TABLE IF NOT EXISTS transaction_tags (
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id         INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (transaction_id, tag_id)
);

CREATE TABLE IF NOT EXISTS tag_suggestions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER,
    transaction_id INTEGER,
    name           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    reviewed       INTEGER NOT NULL DEFAULT 0
);

-- پیام‌های مکالمه (حافظه‌ی کوتاه‌مدت برای ادامه‌ی گفتگو)
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,                    -- user | assistant
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- پروفایل بلندمدت کاربر (خلاصه‌ی شناختی که هر شب آپدیت می‌شود)
CREATE TABLE IF NOT EXISTS user_profile (
    user_id    INTEGER PRIMARY KEY,
    profile    TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

-- صفِ پیام‌های بعد-از-ساعت‌کاری (متنِ رونویسی‌شده) تا صبح یک‌جا ثبت شوند
CREATE TABLE IF NOT EXISTS pending_inputs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',   -- text | voice
    content    TEXT NOT NULL,                  -- متن، یا file_id ویس
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_user_status ON transactions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id, id);
