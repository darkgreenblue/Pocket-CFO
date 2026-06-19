"""لایه‌ی دسترسی به دیتابیس SQLite (CRUD + گزارش‌ها)."""
from __future__ import annotations

import json
import os
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
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    for col, ddl in (
        ("card_chat_id", "ALTER TABLE transactions ADD COLUMN card_chat_id INTEGER"),
        ("card_message_id", "ALTER TABLE transactions ADD COLUMN card_message_id INTEGER"),
    ):
        if col not in cols:
            conn.execute(ddl)


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
) -> int:
    now = datetime.now().isoformat()
    confirmed_at = now if status == "confirmed" else None
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
               (user_id, status, title, amount, currency_display, note,
                mentioned_items, needs_later_completion, transcript, source,
                created_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, status, title, amount, currency_display, note,
                json.dumps(mentioned_items, ensure_ascii=False),
                int(needs_later_completion), transcript, source,
                now, confirmed_at,
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


def reset_user(user_id: int) -> None:
    """تمام دیتای کاربر را پاک می‌کند (ابزار موقتِ تست — انگار کاربر جدید)."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM transaction_tags WHERE transaction_id IN "
            "(SELECT id FROM transactions WHERE user_id = ?)",
            (user_id,),
        )
        conn.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_profile WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM tag_suggestions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM pending_inputs WHERE user_id = ?", (user_id,))


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
    q = "SELECT * FROM transactions WHERE user_id = ?"
    params: list[Any] = [user_id]
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
    """تعداد پیام‌های کاربر (که به LLM رفته) از ابتدای امروز — معیار سقف روزانه."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE user_id = ? AND role = 'user' "
            "AND created_at >= ?",
            (user_id, start_iso),
        ).fetchone()
    return row["c"]


# ---------- گزارش‌ها ----------

# ---------- حافظه‌ی مکالمه و پروفایل ----------

def add_message(user_id: int, role: str, content: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages(user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat()),
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
            "SELECT * FROM transactions WHERE user_id = ? AND status = 'confirmed' "
            "AND created_at >= ? ORDER BY created_at",
            (user_id, start_iso),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = _tags_for(d["id"])
        out.append(d)
    return out
