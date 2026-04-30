"""Связка с TG_PARSER: при completed-заявке пишем в его БД,
чтобы в TG_PARSER stats.py было видно конверсию воркер→бот→лид."""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("bot.linked")

# data.db лежит в соседней папке TG_PARSER (на одном Desktop)
PARSER_DB = Path(__file__).resolve().parent.parent / "TG_PARSER" / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER NOT NULL,
    source TEXT,
    name TEXT,
    phone TEXT,
    offer TEXT,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_compl_user ON bot_completions(tg_user_id);
CREATE INDEX IF NOT EXISTS idx_bot_compl_source ON bot_completions(source);
"""


@contextmanager
def _conn():
    c = sqlite3.connect(PARSER_DB)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    if not PARSER_DB.exists():
        log.info(f"TG_PARSER db не найдена ({PARSER_DB}) — связка отключена")
        return
    try:
        with _conn() as c:
            c.executescript(SCHEMA)
        log.info(f"связка с TG_PARSER db OK: {PARSER_DB}")
    except Exception as e:
        log.warning(f"не смогла подцепиться к TG_PARSER db: {e}")


def record_bot_completion(tg_user_id, source, name, phone, offer):
    if not PARSER_DB.exists():
        return
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO bot_completions (tg_user_id, source, name, phone, offer, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_user_id, source, name, phone, offer,
                 datetime.now(timezone.utc).isoformat()),
            )
            # Если этот юзер был в leads (его наша сетка нашла) — поднимем статус
            row = c.execute("SELECT id FROM leads WHERE tg_user_id=?", (tg_user_id,)).fetchone()
            if row:
                lead_id = row[0]
                c.execute(
                    "UPDATE leads SET status='contact_left', last_touch_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), lead_id),
                )
                c.execute(
                    "INSERT INTO conversions (lead_id, event, ts, meta) VALUES (?, ?, ?, ?)",
                    (lead_id, "contact_left",
                     datetime.now(timezone.utc).isoformat(), f"phone={phone}"),
                )
    except Exception as e:
        log.warning(f"не смогла записать конверсию в TG_PARSER: {e}")
