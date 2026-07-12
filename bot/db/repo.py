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
    txn_cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    for col, ddl in (
        ("card_chat_id", "ALTER TABLE transactions ADD COLUMN card_chat_id INTEGER"),
        ("card_message_id", "ALTER TABLE transactions ADD COLUMN card_message_id INTEGER"),
        ("jyear", "ALTER TABLE transactions ADD COLUMN jyear INTEGER"),
        ("jmonth", "ALTER TABLE transactions ADD COLUMN jmonth INTEGER"),
    ):
        if col not in txn_cols:
            conn.execute(ddl)

    msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "weight" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN weight INTEGER NOT NULL DEFAULT 1")

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
    jyear: Optional[int] = None,
    jmonth: Optional[int] = None,
) -> int:
    now = datetime.now().isoformat()
    confirmed_at = now if status == "confirmed" else None
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
               (user_id, status, title, amount, currency_display, note,
                mentioned_items, needs_later_completion, transcript, source,
                jyear, jmonth, created_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, status, title, amount, currency_display, note,
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
        conn.execute("DELETE FROM goals WHERE user_id = ?", (user_id,))


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
            "SELECT * FROM transactions WHERE user_id = ? AND status = 'confirmed' "
            "AND jyear = ? AND jmonth = ?",
            (user_id, jyear, jmonth),
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
            "INSERT INTO goals(user_id, status, topic, tag_id, limit_amount, jyear, jmonth, "
            "note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, status, topic, tag_id, limit_amount, jyear, jmonth, note,
             datetime.now().isoformat()),
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
    """هدفِ همان ماه با همان تگ (یا همان موضوع اگر تگ ندارد) — برای upsert."""
    with _conn() as conn:
        if tag_id is not None:
            row = conn.execute(
                "SELECT * FROM goals WHERE user_id = ? AND jyear = ? AND jmonth = ? AND tag_id = ?",
                (user_id, jyear, jmonth, tag_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM goals WHERE user_id = ? AND jyear = ? AND jmonth = ? "
                "AND tag_id IS NULL AND topic = ?",
                (user_id, jyear, jmonth, topic),
            ).fetchone()
    return dict(row) if row else None


def active_goals_for_month(user_id: int, jyear: int, jmonth: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM goals WHERE user_id = ? AND jyear = ? AND jmonth = ? AND status = 'active'",
            (user_id, jyear, jmonth),
        ).fetchall()
    return [dict(r) for r in rows]
