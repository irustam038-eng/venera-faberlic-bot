import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    source TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    funnel_stage TEXT NOT NULL DEFAULT 'started',
    name TEXT,
    phone TEXT,
    spend_tier TEXT,
    link_shown_at TEXT,
    reminder1_sent INTEGER NOT NULL DEFAULT 0,
    reminder2_sent INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_stage ON users(funnel_stage);
CREATE INDEX IF NOT EXISTS idx_users_source ON users(source);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER,
    event TEXT NOT NULL,
    payload TEXT,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tg ON events(tg_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN spend_tier TEXT",
    "ALTER TABLE users ADD COLUMN link_shown_at TEXT",
    "ALTER TABLE users ADD COLUMN reminder1_sent INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN reminder2_sent INTEGER NOT NULL DEFAULT 0",
]


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
    # безопасные миграции для существующих БД
    with conn() as c:
        for sql in MIGRATIONS:
            try:
                c.execute(sql)
            except Exception:
                pass


def upsert_user(tg_id, username, first_name, source):
    ts = now()
    with conn() as c:
        existing = c.execute("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if existing:
            c.execute(
                "UPDATE users SET username=?, first_name=?, last_seen=? WHERE tg_id=?",
                (username, first_name, ts, tg_id),
            )
        else:
            c.execute(
                "INSERT INTO users (tg_id, username, first_name, source, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_id, username, first_name, source, ts, ts),
            )


def update_stage(tg_id, stage, **fields):
    fields["funnel_stage"] = stage
    fields["last_seen"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE users SET {sets} WHERE tg_id=?", (*fields.values(), tg_id))


def log_event(tg_id, event, payload=None):
    with conn() as c:
        c.execute(
            "INSERT INTO events (tg_id, event, payload, ts) VALUES (?, ?, ?, ?)",
            (tg_id, event, payload, now()),
        )


def get_user(tg_id):
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(r) if r else None


def mark_link_shown(tg_id):
    with conn() as c:
        c.execute(
            "UPDATE users SET link_shown_at=?, reminder1_sent=0, reminder2_sent=0 WHERE tg_id=?",
            (now(), tg_id)
        )


def mark_reminder1_sent(tg_id):
    with conn() as c:
        c.execute("UPDATE users SET reminder1_sent=1 WHERE tg_id=?", (tg_id,))


def mark_reminder2_sent(tg_id):
    with conn() as c:
        c.execute("UPDATE users SET reminder2_sent=1 WHERE tg_id=?", (tg_id,))


def pending_reminder1(minutes=10):
    """Пользователи кому показали ссылку X минут назад, reminder1 ещё не отправлен."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM users WHERE link_shown_at IS NOT NULL "
            "AND reminder1_sent=0 "
            "AND funnel_stage='showed_link'"
        ).fetchall()
        result = []
        cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
        for r in rows:
            try:
                shown = datetime.fromisoformat(r["link_shown_at"]).timestamp()
                if shown < cutoff:
                    result.append(dict(r))
            except Exception:
                pass
        return result


def pending_reminder2(minutes=40):
    """Пользователи кому отправили reminder1, но они не зарегистрировались."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM users WHERE reminder1_sent=1 "
            "AND reminder2_sent=0 "
            "AND funnel_stage='showed_link'"
        ).fetchall()
        result = []
        cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
        for r in rows:
            try:
                shown = datetime.fromisoformat(r["link_shown_at"]).timestamp()
                if shown < cutoff:
                    result.append(dict(r))
            except Exception:
                pass
        return result


def funnel_stats():
    with conn() as c:
        stages = c.execute(
            "SELECT funnel_stage, COUNT(*) n FROM users GROUP BY funnel_stage"
        ).fetchall()
        sources = c.execute(
            "SELECT source, COUNT(*) total, "
            "SUM(CASE WHEN funnel_stage='registered' THEN 1 ELSE 0 END) registered, "
            "SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) completed "
            "FROM users GROUP BY source ORDER BY total DESC"
        ).fetchall()
        return {
            "stages": [dict(r) for r in stages],
            "sources": [dict(r) for r in sources],
        }