"""لایه‌ی دسترسی به دیتابیس SQLite (CRUD + گزارش‌ها)."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from bot.config import settings

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "tags_seed.json"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
    _seed_tags_if_empty()


def _migrate(conn: sqlite3.Connection) -> None:
    """ستون‌های جدید را روی دیتابیس‌های قدیمی اضافه می‌کند (idempotent)."""
    txn_cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    for col, ddl in (
        ("card_chat_id", "ALTER TABLE transactions ADD COLUMN card_chat_id INTEGER"),
        ("card_message_id", "ALTER TABLE transactions ADD COLUMN card_message_id INTEGER"),
        ("jyear", "ALTER TABLE transactions ADD COLUMN jyear INTEGER"),
        ("jmonth", "ALTER TABLE transactions ADD COLUMN jmonth INTEGER"),
    ):
        if col not in txn_cols:
            conn.execute(ddl)

    if "household_id" not in txn_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN household_id INTEGER")

    msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "weight" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN weight INTEGER NOT NULL DEFAULT 1")

    goal_cols = {r["name"] for r in conn.execute("PRAGMA table_info(goals)").fetchall()}
    if "household_id" not in goal_cols:
        conn.execute("ALTER TABLE goals ADD COLUMN household_id INTEGER")

    # ایندکس‌های household_id فقط بعد از ALTER بالا ساخته می‌شوند؛ در schema.sql نمی‌آیند
    # چون آن فایل روی دیتابیسِ موجود، پیش از افزوده‌شدنِ ستون اجرا می‌شود.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_household "
                 "ON transactions(household_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_household "
                 "ON goals(household_id, jyear, jmonth)")

    # backfill ماه شمسی برای تراکنش‌های قدیمی از created_at
    rows = conn.execute(
        "SELECT id, created_at FROM transactions WHERE jyear IS NULL AND created_at IS NOT NULL"
    ).fetchall()
    if rows:
        import jdatetime
        for r in rows:
            try:
                g = datetime.fromisoformat(r["created_at"]).date()
                jd = jdatetime.date.fromgregorian(date=g)
                conn.execute("UPDATE transactions SET jyear = ?, jmonth = ? WHERE id = ?",
                             (jd.year, jd.month, r["id"]))
            except Exception:  # noqa: BLE001
                pass

    _backfill_households(conn)


def _backfill_households(conn: sqlite3.Connection) -> None:
    """دیتای قبل از فیچرِ خانوار را به یک خانوارِ تک‌نفره برای هر کاربر منتقل می‌کند."""
    user_ids: set[int] = set()
    for table in ("transactions", "goals"):
        rows = conn.execute(
            f"SELECT DISTINCT user_id FROM {table} WHERE household_id IS NULL"
        ).fetchall()
        user_ids.update(r["user_id"] for r in rows)
    for uid in user_ids:
        hid = _ensure_household(conn, uid)
        for table in ("transactions", "goals"):
            conn.execute(
                f"UPDATE {table} SET household_id = ? WHERE user_id = ? AND household_id IS NULL",
                (hid, uid),
            )


def _seed_tags_if_empty() -> None:
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
        if count:
            return
        data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        for parent in data:
            cur = conn.execute(
                "INSERT INTO tags(name, parent_id, level, aliases) VALUES (?, NULL, 1, ?)",
                (parent["name"], json.dumps(parent.get("aliases", []), ensure_ascii=False)),
            )
            parent_id = cur.lastrowid
            for child in parent.get("children", []):
                conn.execute(
                    "INSERT INTO tags(name, parent_id, level, aliases) VALUES (?, ?, 2, ?)",
                    (child["name"], parent_id,
                     json.dumps(child.get("aliases", []), ensure_ascii=False)),
                )


# ---------- خانوار ----------

def _ensure_household(conn: sqlite3.Connection, user_id: int, display_name: str = "") -> int:
    """آی‌دی خانوارِ کاربر؛ اگر عضو هیچ خانواری نبود یک خانوارِ تک‌نفره می‌سازد."""
    row = conn.execute(
        "SELECT household_id FROM household_members WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        return row["household_id"]
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO households(name, owner_id, created_at) VALUES (?, ?, ?)",
        ("خانوار", user_id, now),
    )
    hid = cur.lastrowid
    conn.execute(
        "INSERT INTO household_members(household_id, user_id, role, relation, display_name, "
        "can_set_goals, joined_at) VALUES (?, ?, 'owner', '', ?, 1, ?)",
        (hid, user_id, display_name, now),
    )
    return hid


def household_id_for(user_id: int) -> int:
    """آی‌دی خانوارِ کاربر (در صورت نبود، ساخته می‌شود). مبنای همه‌ی کوئری‌های مالی."""
    with _conn() as conn:
        return _ensure_household(conn, user_id)


def find_household_id(user_id: int) -> Optional[int]:
    """فقط جست‌وجو — چیزی نمی‌سازد (برای بررسی دسترسی)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT household_id FROM household_members WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["household_id"] if row else None


def get_household(household_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM households WHERE id = ?", (household_id,)).fetchone()
    return dict(row) if row else None


def household_members(household_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM household_members WHERE household_id = ? "
            "ORDER BY (role = 'owner') DESC, joined_at",
            (household_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_member(user_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM household_members WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def touch_member(user_id: int, display_name: str = "") -> int:
    """اطمینان از وجود خانوار + به‌روزرسانیِ نامِ نمایشی (برای گزارشِ ثبت‌کننده)."""
    with _conn() as conn:
        hid = _ensure_household(conn, user_id, display_name)
        if display_name:
            conn.execute(
                "UPDATE household_members SET display_name = ? WHERE user_id = ? "
                "AND display_name != ?",
                (display_name, user_id, display_name),
            )
        return hid


def update_member(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE household_members SET {cols} WHERE user_id = ?",
                     (*fields.values(), user_id))


def remove_member(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM household_members WHERE user_id = ?", (user_id,))


def all_member_user_ids() -> list[int]:
    """همه‌ی کاربرانی که ربات می‌شناسد (برای jobهای شبانه — نه فقط ALLOWED_USER_IDS)."""
    with _conn() as conn:
        rows = conn.execute("SELECT user_id FROM household_members").fetchall()
    return [r["user_id"] for r in rows]


def create_invite(*, household_id: int, created_by: int, relation: str,
                  can_set_goals: bool) -> str:
    token = secrets.token_urlsafe(12).replace("-", "_")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO household_invites(token, household_id, created_by, relation, "
            "can_set_goals, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, household_id, created_by, relation, int(can_set_goals),
             datetime.now().isoformat()),
        )
    return token


def get_invite(token: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM household_invites WHERE token = ?", (token,)
        ).fetchone()
    return dict(row) if row else None


def mark_invite_used(token: str, user_id: int) -> bool:
    """دعوت را یک‌بارمصرف می‌کند؛ False یعنی قبلاً استفاده شده (رقابتِ هم‌زمان)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE household_invites SET used_at = ?, used_by = ? "
            "WHERE token = ? AND used_at IS NULL",
            (datetime.now().isoformat(), user_id, token),
        )
        return cur.rowcount > 0


def join_household(*, household_id: int, user_id: int, relation: str,
                   can_set_goals: bool, display_name: str = "") -> None:
    """کاربر را به خانوار اضافه می‌کند و خانوارِ تک‌نفره‌ی قبلی‌اش را در آن ادغام می‌کند."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT household_id FROM household_members WHERE user_id = ?", (user_id,)
        ).fetchone()
        old_hid = row["household_id"] if row else None
        if old_hid == household_id:
            conn.execute(
                "UPDATE household_members SET relation = ?, can_set_goals = ? WHERE user_id = ?",
                (relation, int(can_set_goals), user_id),
            )
            return
        if old_hid is not None:
            # دیتای دفترِ قبلی‌اش به دفترِ مشترک منتقل می‌شود تا چیزی گم نشود.
            for table in ("transactions", "goals", "debts"):
                conn.execute(f"UPDATE {table} SET household_id = ? WHERE household_id = ?",
                             (household_id, old_hid))
            conn.execute("DELETE FROM household_members WHERE user_id = ?", (user_id,))
            others = conn.execute(
                "SELECT 1 FROM household_members WHERE household_id = ? LIMIT 1", (old_hid,)
            ).fetchone()
            if not others:
                conn.execute("DELETE FROM households WHERE id = ?", (old_hid,))
        conn.execute(
            "INSERT INTO household_members(household_id, user_id, role, relation, display_name, "
            "can_set_goals, joined_at) VALUES (?, ?, 'member', ?, ?, ?, ?)",
            (household_id, user_id, relation, display_name, int(can_set_goals),
             datetime.now().isoformat()),
        )


# ---------- تگ‌ها ----------

def get_tags() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT id, name, parent_id, level, aliases FROM tags").fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "parent_id": r["parent_id"],
            "level": r["level"],
            "aliases": json.loads(r["aliases"] or "[]"),
        }
        for r in rows
    ]


def top_level_for_tag(tag_id: int) -> Optional[str]:
    """نام تگ سطح‌بالا (ریشه) برای یک تگ مشخص را برمی‌گرداند."""
    with _conn() as conn:
        row = conn.execute("SELECT id, name, parent_id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        while row and row["parent_id"] is not None:
            row = conn.execute(
                "SELECT id, name, parent_id FROM tags WHERE id = ?", (row["parent_id"],)
            ).fetchone()
    return row["name"] if row else None


def add_tag_suggestion(user_id: int, transaction_id: int, name: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tag_suggestions(user_id, transaction_id, name, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, transaction_id, name, datetime.now().isoformat()),
        )


# ---------- تراکنش‌ها ----------

def create_transaction(
    *,
    user_id: int,
    title: Optional[str],
    amount: Optional[int],
    currency_display: str,
    note: str,
    mentioned_items: list[str],
    needs_later_completion: bool,
    transcript: str,
    source: str,
    status: str = "draft",
    jyear: Optional[int] = None,
    jmonth: Optional[int] = None,
) -> int:
    now = datetime.now().isoformat()
    confirmed_at = now if status == "confirmed" else None
    with _conn() as conn:
        household_id = _ensure_household(conn, user_id)
        cur = conn.execute(
            """INSERT INTO transactions
               (user_id, household_id, status, title, amount, currency_display, note,
                mentioned_items, needs_later_completion, transcript, source,
                jyear, jmonth, created_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, household_id, status, title, amount, currency_display, note,
                json.dumps(mentioned_items, ensure_ascii=False),
                int(needs_later_completion), transcript, source,
                jyear, jmonth, now, confirmed_at,
            ),
        )
        return cur.lastrowid


def set_card_message(txn_id: int, chat_id: int, message_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE transactions SET card_chat_id = ?, card_message_id = ? WHERE id = ?",
            (chat_id, message_id, txn_id),
        )


def find_by_card_message(chat_id: int, message_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM transactions WHERE card_chat_id = ? AND card_message_id = ?",
            (chat_id, message_id),
        ).fetchone()
    return get_transaction(row["id"]) if row else None


def get_transaction(txn_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["mentioned_items"] = json.loads(data.get("mentioned_items") or "[]")
    data["tags"] = _tags_for(txn_id)
    return data


def _tags_for(txn_id: int) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT t.name FROM transaction_tags tt JOIN tags t ON t.id = tt.tag_id "
            "WHERE tt.transaction_id = ? ORDER BY t.level",
            (txn_id,),
        ).fetchall()
    return [r["name"] for r in rows]


def update_transaction(txn_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE transactions SET {cols} WHERE id = ?", (*fields.values(), txn_id))


def confirm_transaction(txn_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE transactions SET status = 'confirmed', confirmed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), txn_id),
        )


def delete_transaction(txn_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))


def add_transaction_tag(txn_id: int, tag_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO transaction_tags(transaction_id, tag_id) VALUES (?, ?)",
            (txn_id, tag_id),
        )


def pending_for_reminder(user_id: int) -> list[dict[str, Any]]:
    """تراکنش‌هایی که باید در یادآوری شبانه پیگیری شوند."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM transactions
               WHERE user_id = ? AND reminded = 0 AND (
                   needs_later_completion = 1
                   OR (status = 'draft' AND (amount IS NULL OR title IS NULL OR title = ''))
               )""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_reminded(txn_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE transactions SET reminded = 1 WHERE id = ?", (txn_id,))


def add_pending(user_id: int, kind: str, content: str) -> None:
    if content and content.strip():
        with _conn() as conn:
            conn.execute(
                "INSERT INTO pending_inputs(user_id, kind, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, kind, content.strip(), datetime.now().isoformat()),
            )


def get_pending(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT kind, content FROM pending_inputs WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def has_pending(user_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pending_inputs WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
    return row is not None


def clear_pending(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM pending_inputs WHERE user_id = ?", (user_id,))


def users_with_pending() -> list[int]:
    with _conn() as conn:
        rows = conn.execute("SELECT DISTINCT user_id FROM pending_inputs").fetchall()
    return [r["user_id"] for r in rows]


# ---------- درِ دومِ ورودی (شرتکات) ----------

def issue_ingest_token(user_id: int, token: str) -> None:
    """توکنِ تازه برای کاربر صادر می‌کند و توکنِ قبلی‌اش را باطل می‌کند (rotate)."""
    with _conn() as conn:
        conn.execute("DELETE FROM ingest_tokens WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO ingest_tokens(token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, datetime.now().isoformat()),
        )


def ingest_token_owner(token: str) -> Optional[int]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM ingest_tokens WHERE token = ?", (token,)
        ).fetchone()
    return row["user_id"] if row else None


def all_ingest_tokens() -> dict[str, int]:
    """همه‌ی توکن‌ها — برای مقایسه‌ی constant-time روی کلِ مجموعه."""
    with _conn() as conn:
        rows = conn.execute("SELECT token, user_id FROM ingest_tokens").fetchall()
    return {r["token"]: r["user_id"] for r in rows}


def touch_ingest_token(token: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE ingest_tokens SET last_used_at = ? WHERE token = ?",
            (datetime.now().isoformat(), token),
        )


def revoke_ingest_tokens(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM ingest_tokens WHERE user_id = ?", (user_id,))


def find_ingest_request(request_id: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT request_id, user_id, status, message FROM ingest_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return dict(row) if row else None


def claim_ingest_request(request_id: str, user_id: int) -> bool:
    """جای این درخواست را رزرو می‌کند. False یعنی قبلاً دیده‌ایمش (تکراری).

    رزروِ اتمیک است تا دو POSTِ هم‌زمانِ یک درخواست دو تراکنش نسازند.
    """
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ingest_requests(request_id, user_id, status, message, created_at) "
            "VALUES (?, ?, 'processing', '', ?)",
            (request_id, user_id, datetime.now().isoformat()),
        )
        return cur.rowcount > 0


def finish_ingest_request(request_id: str, status: str, message: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE ingest_requests SET status = ?, message = ? WHERE request_id = ?",
            (status, message, request_id),
        )


def release_ingest_request(request_id: str) -> None:
    """رزرو را پس می‌گیرد تا کاربر بتواند همان درخواست را دوباره بفرستد."""
    with _conn() as conn:
        conn.execute("DELETE FROM ingest_requests WHERE request_id = ?", (request_id,))


def purge_ingest_requests(before_iso: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM ingest_requests WHERE created_at < ?", (before_iso,))


def undelivered_cards(source: str, since_iso: str) -> list[dict[str, Any]]:
    """تراکنش‌هایی که ساخته شدند ولی کارتشان به تلگرام نرسید (برای تلاشِ دوباره)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id FROM transactions "
            "WHERE source = ? AND card_message_id IS NULL AND created_at >= ? ORDER BY id",
            (source, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def sync_status(txn_id: int) -> str:
    """اگر تراکنش مبلغ و عنوان داشت → confirmed، وگرنه draft. وضعیت نهایی را برمی‌گرداند."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT title, amount, status FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
        if not row:
            return "deleted"
        complete = row["amount"] is not None and bool((row["title"] or "").strip())
        new_status = "confirmed" if complete else "draft"
        if new_status != row["status"]:
            confirmed_at = datetime.now().isoformat() if new_status == "confirmed" else None
            conn.execute(
                "UPDATE transactions SET status = ?, confirmed_at = ? WHERE id = ?",
                (new_status, confirmed_at, txn_id),
            )
    return new_status


def list_user_transactions(user_id: int, start_iso: Optional[str] = None,
                           include_drafts: bool = True) -> list[dict[str, Any]]:
    """تراکنش‌های **خانوارِ** کاربر (نه فقط خودش) — عملکردِ تجمعیِ خانوار."""
    q = "SELECT * FROM transactions WHERE household_id = ?"
    params: list[Any] = [household_id_for(user_id)]
    if not include_drafts:
        q += " AND status = 'confirmed'"
    if start_iso:
        q += " AND created_at >= ?"
        params.append(start_iso)
    q += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["mentioned_items"] = json.loads(d.get("mentioned_items") or "[]")
        d["tags"] = _tags_for(d["id"])
        out.append(d)
    return out


def count_llm_messages_today(user_id: int, start_iso: str) -> int:
    """مجموع کوپن‌های مصرف‌شده‌ی امروز (پیام چانک‌شده وزن>۱ دارد) — معیار سقف روزانه."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(weight), 0) AS c FROM messages WHERE user_id = ? "
            "AND role = 'user' AND created_at >= ?",
            (user_id, start_iso),
        ).fetchone()
    return row["c"]


# ---------- گزارش‌ها ----------

# ---------- حافظه‌ی مکالمه و پروفایل ----------

def add_message(user_id: int, role: str, content: str, weight: int = 1) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages(user_id, role, content, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, role, content, weight, datetime.now().isoformat()),
        )


def recent_messages(user_id: int, limit: int) -> list[dict[str, Any]]:
    """آخرین پیام‌ها به ترتیب زمانی صعودی (قدیمی→جدید)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def messages_since(user_id: int, since_iso: str) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? AND created_at >= ? "
            "ORDER BY id",
            (user_id, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def get_profile(user_id: int) -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT profile FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["profile"] if row else ""


def set_profile(user_id: int, profile: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_profile(user_id, profile, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET profile = excluded.profile, "
            "updated_at = excluded.updated_at",
            (user_id, profile, datetime.now().isoformat()),
        )


def users_with_messages_since(since_iso: str) -> list[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM messages WHERE created_at >= ?", (since_iso,)
        ).fetchall()
    return [r["user_id"] for r in rows]


def confirmed_in_range(user_id: int, start_iso: str) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE household_id = ? AND status = 'confirmed' "
            "AND created_at >= ? ORDER BY created_at",
            (_ensure_household(conn, user_id), start_iso),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = _tags_for(d["id"])
        out.append(d)
    return out


# ---------- تگ‌ها: نوادگان ----------

def tag_descendant_names(tag_id: int) -> set[str]:
    """نام خودِ تگ + همه‌ی زیرشاخه‌هایش (بازگشتی)."""
    with _conn() as conn:
        names: set[str] = set()
        frontier = [tag_id]
        while frontier:
            tid = frontier.pop()
            row = conn.execute("SELECT name FROM tags WHERE id = ?", (tid,)).fetchone()
            if row:
                names.add(row["name"])
            children = conn.execute("SELECT id FROM tags WHERE parent_id = ?", (tid,)).fetchall()
            frontier.extend(c["id"] for c in children)
    return names


def confirmed_in_jmonth(user_id: int, jyear: int, jmonth: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE household_id = ? AND status = 'confirmed' "
            "AND jyear = ? AND jmonth = ?",
            (_ensure_household(conn, user_id), jyear, jmonth),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["mentioned_items"] = json.loads(d.get("mentioned_items") or "[]")
        d["tags"] = _tags_for(d["id"])
        out.append(d)
    return out


# ---------- اهداف مالی ----------

def create_goal(*, user_id: int, topic: Optional[str], tag_id: Optional[int],
                limit_amount: Optional[int], jyear: int, jmonth: int,
                note: str = "", status: str = "draft") -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO goals(user_id, household_id, status, topic, tag_id, limit_amount, "
            "jyear, jmonth, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, _ensure_household(conn, user_id), status, topic, tag_id, limit_amount,
             jyear, jmonth, note, datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_goal(goal_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def update_goal(goal_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE goals SET {cols} WHERE id = ?", (*fields.values(), goal_id))


def delete_goal(goal_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def set_goal_card(goal_id: int, chat_id: int, message_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE goals SET card_chat_id = ?, card_message_id = ? WHERE id = ?",
                     (chat_id, message_id, goal_id))


def find_goal_by_card_message(chat_id: int, message_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM goals WHERE card_chat_id = ? AND card_message_id = ?",
            (chat_id, message_id),
        ).fetchone()
    return get_goal(row["id"]) if row else None


def find_goal_by_tag_month(user_id: int, tag_id: Optional[int], topic: Optional[str],
                           jyear: int, jmonth: int) -> Optional[dict[str, Any]]:
    """هدفِ همان ماه با همان تگ (یا همان موضوع اگر تگ ندارد) — برای upsert.

    جست‌وجو در سطحِ خانوار است: اگر پارتنر قبلاً روی همان دسته هدف گذاشته، هدفِ موازیِ
    دوم ساخته نمی‌شود و همان هدفِ مشترک به‌روزرسانی می‌شود.
    """
    with _conn() as conn:
        hid = _ensure_household(conn, user_id)
        if tag_id is not None:
            row = conn.execute(
                "SELECT * FROM goals WHERE household_id = ? AND jyear = ? AND jmonth = ? "
                "AND tag_id = ?",
                (hid, jyear, jmonth, tag_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM goals WHERE household_id = ? AND jyear = ? AND jmonth = ? "
                "AND tag_id IS NULL AND topic = ?",
                (hid, jyear, jmonth, topic),
            ).fetchone()
    return dict(row) if row else None


def active_goals_for_month(user_id: int, jyear: int, jmonth: int) -> list[dict[str, Any]]:
    """اهدافِ فعالِ خانوار در آن ماه (هدفِ هر عضو، هدفِ همه است)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM goals WHERE household_id = ? AND jyear = ? AND jmonth = ? "
            "AND status = 'active'",
            (_ensure_household(conn, user_id), jyear, jmonth),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- بدهی و طلب ----------

def create_debt(*, user_id: int, kind: str, counterparty: Optional[str], title: Optional[str],
                amount: Optional[int], currency_display: str, due_text: Optional[str] = None,
                note: str = "", status: str = "draft", jyear: Optional[int] = None,
                jmonth: Optional[int] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO debts(user_id, household_id, kind, status, counterparty, title, amount, "
            "currency_display, due_text, note, jyear, jmonth, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, _ensure_household(conn, user_id), kind, status, counterparty, title,
             amount, currency_display, due_text, note, jyear, jmonth,
             datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_debt(debt_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    return dict(row) if row else None


def update_debt(debt_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE debts SET {cols} WHERE id = ?", (*fields.values(), debt_id))


def delete_debt(debt_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM debts WHERE id = ?", (debt_id,))


def set_debt_card(debt_id: int, chat_id: int, message_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE debts SET card_chat_id = ?, card_message_id = ? WHERE id = ?",
                     (chat_id, message_id, debt_id))


def find_debt_by_card_message(chat_id: int, message_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM debts WHERE card_chat_id = ? AND card_message_id = ?",
            (chat_id, message_id),
        ).fetchone()
    return get_debt(row["id"]) if row else None


def find_open_debt(user_id: int, kind: str, counterparty: Optional[str]) -> Optional[dict[str, Any]]:
    """بدهی/طلبِ بازِ همان طرف‌حساب در خانوار — برای اینکه تسویه‌ی بعدی سراغ همان برود."""
    if not counterparty:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM debts WHERE household_id = ? AND kind = ? AND status != 'settled' "
            "AND counterparty = ? ORDER BY id DESC LIMIT 1",
            (_ensure_household(conn, user_id), kind, counterparty),
        ).fetchone()
    return dict(row) if row else None


def list_debts(user_id: int, *, kind: Optional[str] = None,
               include_settled: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM debts WHERE household_id = ?"
    params: list[Any] = [household_id_for(user_id)]
    if kind:
        q += " AND kind = ?"
        params.append(kind)
    if not include_settled:
        q += " AND status != 'settled'"
    q += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]
