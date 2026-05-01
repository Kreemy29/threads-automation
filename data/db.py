import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            username        TEXT PRIMARY KEY,
            adspower_id     TEXT NOT NULL,
            state           TEXT DEFAULT 'pending',
            setup_done      INTEGER DEFAULT 0,
            warmup_done     INTEGER DEFAULT 0,
            warmup_start    TEXT,
            telegram_posted INTEGER DEFAULT 0,
            telegram_pinned INTEGER DEFAULT 0,
            daily_posts     INTEGER DEFAULT 0,
            daily_comments  INTEGER DEFAULT 0,
            last_post_time  TEXT,
            last_comment_time TEXT,
            last_follow_time  TEXT,
            last_unfollow_time TEXT,
            media_folder    TEXT DEFAULT '',
            follow_list     TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS follow_lists (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL UNIQUE,
            handles TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS actions_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            action      TEXT NOT NULL,
            status      TEXT NOT NULL,
            details     TEXT,
            ts          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS follow_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            target      TEXT NOT NULL,
            followed_at TEXT,
            unfollow_at TEXT,
            unfollowed  INTEGER DEFAULT 0
        );
    """)
    conn.commit()

    # ── migrations: add columns that may not exist in older DBs ──
    for col, definition in [
        ("media_folder",  "TEXT DEFAULT ''"),
        ("follow_list",   "TEXT DEFAULT ''"),
        ("retry_count",   "INTEGER DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.close()


def load_accounts_from_file(filepath):
    """Read accounts.txt and upsert accounts.
    Supported formats per line:
      username,adspower_id
      username,adspower_id,media_folder
      username,adspower_id,media_folder,follow_list_name
    """
    conn = _connect()
    c = conn.cursor()
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            username    = parts[0].lstrip("@")
            adspower_id = parts[1]
            media_folder = parts[2] if len(parts) > 2 else ""
            follow_list  = parts[3] if len(parts) > 3 else ""
            c.execute(
                "INSERT INTO accounts (username, adspower_id, media_folder, follow_list) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET "
                "adspower_id=excluded.adspower_id, "
                "media_folder=CASE WHEN excluded.media_folder!='' THEN excluded.media_folder ELSE media_folder END, "
                "follow_list=CASE WHEN excluded.follow_list!='' THEN excluded.follow_list ELSE follow_list END",
                (username, adspower_id, media_folder, follow_list),
            )
    conn.commit()
    conn.close()


def get_pending_accounts(limit):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM accounts WHERE state IN ('pending','setup','warmup','error','active') ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_running_accounts():
    """Reset accounts stuck at 'running' from a previous crash back to 'error'."""
    conn = _connect()
    conn.execute(
        "UPDATE accounts SET state='error', notes='reset from stuck running state' "
        "WHERE state='running'"
    )
    conn.commit()
    conn.close()


def get_account(username):
    conn = _connect()
    row = conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_account(username, **kwargs):
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [username]
    conn = _connect()
    conn.execute(f"UPDATE accounts SET {fields} WHERE username = ?", values)
    conn.commit()
    conn.close()


def log_action(username, action, status, details=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO actions_log (username, action, status, details) VALUES (?, ?, ?, ?)",
        (username, action, status, details),
    )
    conn.commit()
    conn.close()


def add_to_follow_queue(username, targets, unfollow_after_seconds):
    from datetime import timedelta
    conn = _connect()
    now = datetime.utcnow()
    unfollow_at = (now + timedelta(seconds=unfollow_after_seconds)).isoformat()
    for target in targets:
        conn.execute(
            "INSERT INTO follow_queue (username, target, followed_at, unfollow_at) VALUES (?, ?, ?, ?)",
            (username, target, now.isoformat(), unfollow_at),
        )
    conn.commit()
    conn.close()


def get_due_unfollows(username):
    conn = _connect()
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT * FROM follow_queue WHERE username = ? AND unfollowed = 0 AND unfollow_at <= ?",
        (username, now),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_unfollowed(follow_id):
    conn = _connect()
    conn.execute("UPDATE follow_queue SET unfollowed = 1 WHERE id = ?", (follow_id,))
    conn.commit()
    conn.close()


def save_follow_list(name: str, handles: list):
    conn = _connect()
    conn.execute(
        "INSERT INTO follow_lists (name, handles) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET handles=excluded.handles",
        (name, "\n".join(h.strip().lstrip("@") for h in handles if h.strip())),
    )
    conn.commit()
    conn.close()


def get_follow_list(name: str) -> list:
    conn = _connect()
    row = conn.execute("SELECT handles FROM follow_lists WHERE name=?", (name,)).fetchone()
    conn.close()
    if not row or not row["handles"]:
        return []
    return [h for h in row["handles"].splitlines() if h.strip()]


def list_follow_lists() -> list:
    conn = _connect()
    rows = conn.execute("SELECT name FROM follow_lists ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def delete_follow_list(name: str):
    conn = _connect()
    conn.execute("DELETE FROM follow_lists WHERE name=?", (name,))
    conn.commit()
    conn.close()


def get_daily_counts(username):
    """Returns posts and comments done today."""
    conn = _connect()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    posts = conn.execute(
        "SELECT COUNT(*) FROM actions_log WHERE username=? AND action='text_post' AND status='ok' AND ts LIKE ?",
        (username, f"{today}%"),
    ).fetchone()[0]
    img_posts = conn.execute(
        "SELECT COUNT(*) FROM actions_log WHERE username=? AND action='image_post' AND status='ok' AND ts LIKE ?",
        (username, f"{today}%"),
    ).fetchone()[0]
    comments = conn.execute(
        "SELECT COUNT(*) FROM actions_log WHERE username=? AND action='outreach_comment' AND status='ok' AND ts LIKE ?",
        (username, f"{today}%"),
    ).fetchone()[0]
    conn.close()
    return {"posts": posts + img_posts, "comments": comments}
