CREATE TABLE IF NOT EXISTS tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES tags(id),
    level     INTEGER NOT NULL DEFAULT 1,
    aliases   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS transactions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL,                 -- ثبت‌کننده (چه کسی وارد کرد)
    household_id           INTEGER,                          -- دفترِ مشترکی که در آن ثبت شده
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
    jyear                  INTEGER,
    jmonth                 INTEGER,
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
    weight     INTEGER NOT NULL DEFAULT 1,       -- تعداد کوپنِ مصرف‌شده (پیام چانک‌شده >1)
    created_at TEXT NOT NULL
);

-- اهداف مالی (لیمیت ماهانه روی یک موضوع)
CREATE TABLE IF NOT EXISTS goals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,                -- ثبت‌کننده‌ی هدف
    household_id     INTEGER,                         -- هدف متعلق به خانوار است، نه یک نفر
    status           TEXT NOT NULL DEFAULT 'draft',   -- draft | active
    topic            TEXT,
    tag_id           INTEGER REFERENCES tags(id),
    limit_amount     INTEGER,                          -- تومان
    jyear            INTEGER NOT NULL,
    jmonth           INTEGER NOT NULL,
    note             TEXT,
    last_alert_level INTEGER NOT NULL DEFAULT 0,       -- 0 | 50 | 80 | 100
    card_chat_id     INTEGER,
    card_message_id  INTEGER,
    created_at       TEXT NOT NULL
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
    kind       TEXT NOT NULL DEFAULT 'text',   -- text | voice | voice_file
    content    TEXT NOT NULL,                  -- متن، یا file_id ویس، یا مسیرِ فایلِ ویس
    created_at TEXT NOT NULL
);

-- درخواست‌های درِ دوم (شرتکاتِ iOS). کلید همان request_id ای است که خودِ شرتکات
-- می‌سازد؛ اگر شبکه قطع شد و کاربر دوباره فرستاد، تراکنشِ تکراری ساخته نمی‌شود.
CREATE TABLE IF NOT EXISTS ingest_requests (
    request_id TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    status     TEXT NOT NULL,                  -- recorded | queued | nothing | error
    message    TEXT NOT NULL DEFAULT '',       -- همان چیزی که به شرتکات پس داده شد
    created_at TEXT NOT NULL
);

-- ─────────────────────────── خانوار (مالیِ مشترک) ───────────────────────────
-- هر کاربر دقیقاً عضو یک خانوار است. کاربرِ تنها هم یک خانوارِ تک‌نفره دارد تا کل
-- سیستم یک‌شکل بماند؛ با join شدنِ پارتنر، همان دفتر مشترک می‌شود.
CREATE TABLE IF NOT EXISTS households (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT 'خانوار',
    owner_id   INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS household_members (
    household_id  INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',   -- owner | member
    relation      TEXT NOT NULL DEFAULT '',         -- پارتنر | همسر | فرزند | ...
    display_name  TEXT NOT NULL DEFAULT '',
    can_set_goals INTEGER NOT NULL DEFAULT 1,       -- اجازه‌ی هدف‌گذاری در خانوار
    joined_at     TEXT NOT NULL,
    PRIMARY KEY (household_id, user_id)
);

-- هر کاربر فقط در یک خانوار (فاز اول: بدون نیاز به اسکیل)
CREATE UNIQUE INDEX IF NOT EXISTS idx_member_user ON household_members(user_id);

-- لینک دعوتِ یک‌بارمصرف؛ نسبت و سطح دسترسی از قبل روی خودِ لینک نشسته‌اند.
CREATE TABLE IF NOT EXISTS household_invites (
    token         TEXT PRIMARY KEY,
    household_id  INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    created_by    INTEGER NOT NULL,
    relation      TEXT NOT NULL DEFAULT '',
    can_set_goals INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    used_at       TEXT,
    used_by       INTEGER
);

-- ─────────────────────────── بدهی و طلب ───────────────────────────
-- kind = debt  → من/خانوار به کسی بدهکاریم
-- kind = credit→ کسی به من/خانوار بدهکار است (طلب)
CREATE TABLE IF NOT EXISTS debts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,                 -- ثبت‌کننده
    household_id     INTEGER,
    kind             TEXT NOT NULL DEFAULT 'debt',     -- debt | credit
    status           TEXT NOT NULL DEFAULT 'draft',    -- draft | open | settled
    counterparty     TEXT,                             -- طرف حساب
    title            TEXT,
    amount           INTEGER,                          -- عدد خام در واحد currency_display
    currency_display TEXT NOT NULL DEFAULT 'toman',
    settled_amount   INTEGER NOT NULL DEFAULT 0,       -- چقدرش تسویه شده
    due_text         TEXT,                             -- سررسید به بیانِ خودِ کاربر
    note             TEXT NOT NULL DEFAULT '',
    card_chat_id     INTEGER,
    card_message_id  INTEGER,
    jyear            INTEGER,
    jmonth           INTEGER,
    created_at       TEXT NOT NULL,
    settled_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_txn_user_status ON transactions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id, id);
CREATE INDEX IF NOT EXISTS idx_debt_household ON debts(household_id, status);

-- ⚠️ ایندکسِ ستون‌هایی که با ALTER TABLE اضافه می‌شوند (household_id روی transactions و
-- goals) اینجا نمی‌آید: این فایل قبل از مهاجرت اجرا می‌شود و روی دیتابیسِ موجود، ستون
-- هنوز ساخته نشده است. آن‌ها در _migrate() و بعد از ALTER ساخته می‌شوند.
