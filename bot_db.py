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
    completed_at TEXT,
    reminder_sent INTEGER NOT NULL DEFAULT 0
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
        try:
            c.execute("ALTER TABLE users ADD COLUMN spend_tier TEXT")
        except Exception:
            pass  # колонка уже существует


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


def pending_reminders(stuck_minutes=30):
    cutoff = (datetime.now(timezone.utc).timestamp() - stuck_minutes * 60)
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM users WHERE reminder_sent=0 AND completed_at IS NULL "
            "AND funnel_stage IN ('quiz_started', 'asked_name', 'asked_phone')"
        ).fetchall()
        result = []
        for r in rows:
            try:
                last = datetime.fromisoformat(r["last_seen"]).timestamp()
                if last < cutoff:
                    result.append(dict(r))
            except Exception:
                pass
        return result


def mark_reminder_sent(tg_id):
    with conn() as c:
        c.execute("UPDATE users SET reminder_sent=1 WHERE tg_id=?", (tg_id,))


def funnel_stats():
    with conn() as c:
        stages = c.execute(
            "SELECT funnel_stage, COUNT(*) n FROM users GROUP BY funnel_stage"
        ).fetchall()
        sources = c.execute(
            "SELECT source, COUNT(*) total, SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) completed "
            "FROM users GROUP BY source ORDER BY total DESC"
        ).fetchall()
        return {
            "stages": [dict(r) for r in stages],
            "sources": [dict(r) for r in sources],
        }
