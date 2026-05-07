import csv
import io
import sqlite3
import os
from contextlib import closing
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode lets readers and writers coexist without "database is locked"
    # under multi-worker access. Idempotent — safe to set on every connect.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_db():
    with closing(_connect()) as conn:
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
                last_post_time  TEXT,
                last_comment_time TEXT,
                last_follow_time  TEXT,
                last_unfollow_time TEXT,
                media_folder    TEXT DEFAULT '',
                follow_list     TEXT DEFAULT '',
                retry_count     INTEGER DEFAULT 0,
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


# Columns that may be set via update_account — guards against typo'd kwargs
# silently no-oping and SQL injection if a caller ever forwards user input.
_UPDATABLE_COLUMNS = frozenset({
    "adspower_id", "state", "setup_done", "warmup_done", "warmup_start",
    "telegram_posted", "telegram_pinned",
    "last_post_time", "last_comment_time", "last_follow_time", "last_unfollow_time",
    "media_folder", "follow_list", "retry_count", "updated_at", "notes",
})


def load_accounts_from_file(filepath):
    """Read accounts.txt and upsert accounts.
    Supported formats per line (CSV — paths may contain commas if quoted):
      username,adspower_id
      username,adspower_id,media_folder
      username,adspower_id,media_folder,follow_list_name
    """
    with open(filepath, "r", encoding="utf-8") as f:
        # Strip blank/comment lines before handing to csv so commented-out
        # lines that happen to contain commas don't trip up the parser.
        cleaned = io.StringIO()
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                cleaned.write(line)
        cleaned.seek(0)
        reader = csv.reader(cleaned)
        rows = list(reader)

    with closing(_connect()) as conn:
        c = conn.cursor()
        # Remove any stale records whose username starts with "@" (from before the lstrip fix)
        c.execute("DELETE FROM accounts WHERE username LIKE '@%'")
        conn.commit()
        for parts in rows:
            parts = [p.strip() for p in parts]
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


def get_pending_accounts(limit):
    # Resume already-running lifecycle states first (active/warmup/setup) so
    # an in-progress account isn't queued behind a brand-new pending one.
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM accounts
            WHERE state IN ('pending','setup','warmup','error','active')
            ORDER BY
                CASE state
                    WHEN 'active'  THEN 0
                    WHEN 'warmup'  THEN 1
                    WHEN 'setup'   THEN 2
                    WHEN 'pending' THEN 3
                    WHEN 'error'   THEN 4
                    ELSE 5
                END,
                created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def reset_running_accounts():
    """Reset accounts stuck at 'running' from a previous crash back to 'error'."""
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE accounts SET state='error', notes='reset from stuck running state' "
            "WHERE state='running'"
        )
        conn.commit()


def get_account(username):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def update_account(username, **kwargs):
    if not kwargs:
        return
    bad = [k for k in kwargs if k not in _UPDATABLE_COLUMNS]
    if bad:
        raise ValueError(f"update_account: unknown column(s) {bad}")
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [username]
    with closing(_connect()) as conn:
        conn.execute(f"UPDATE accounts SET {fields} WHERE username = ?", values)
        conn.commit()


def log_action(username, action, status, details=""):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO actions_log (username, action, status, details) VALUES (?, ?, ?, ?)",
            (username, action, status, details),
        )
        conn.commit()


def add_to_follow_queue(username, targets, unfollow_after_seconds):
    from datetime import timedelta
    now = datetime.utcnow()
    unfollow_at = (now + timedelta(seconds=unfollow_after_seconds)).isoformat()
    with closing(_connect()) as conn:
        for target in targets:
            conn.execute(
                "INSERT INTO follow_queue (username, target, followed_at, unfollow_at) VALUES (?, ?, ?, ?)",
                (username, target, now.isoformat(), unfollow_at),
            )
        conn.commit()


def get_due_unfollows(username):
    now = datetime.utcnow().isoformat()
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM follow_queue WHERE username = ? AND unfollowed = 0 AND unfollow_at <= ?",
            (username, now),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_unfollowed(follow_id):
    with closing(_connect()) as conn:
        conn.execute("UPDATE follow_queue SET unfollowed = 1 WHERE id = ?", (follow_id,))
        conn.commit()


def save_follow_list(name: str, handles: list):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO follow_lists (name, handles) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET handles=excluded.handles",
            (name, "\n".join(h.strip().lstrip("@") for h in handles if h.strip())),
        )
        conn.commit()


def get_follow_list(name: str) -> list:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT handles FROM follow_lists WHERE name=?", (name,)).fetchone()
    if not row or not row["handles"]:
        return []
    return [h for h in row["handles"].splitlines() if h.strip()]


def list_follow_lists() -> list:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT name FROM follow_lists ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def delete_follow_list(name: str):
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM follow_lists WHERE name=?", (name,))
        conn.commit()


def get_daily_counts(username):
    """Returns posts and comments done today."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with closing(_connect()) as conn:
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
    return {"posts": posts + img_posts, "comments": comments}
