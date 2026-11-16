"""SQLite storage.

One connection is shared by the whole process. Every request handler and the
background scheduler run on the event-loop thread, so access is serialized
without extra locking.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token             TEXT NOT NULL UNIQUE,
    link_code         TEXT NOT NULL UNIQUE,
    patient_name      TEXT NOT NULL,
    phone             TEXT NOT NULL DEFAULT '',
    channel           TEXT NOT NULL DEFAULT 'whatsapp',
    language          TEXT NOT NULL DEFAULT 'hy',
    tg_chat_id        TEXT,
    notes             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'draft',
    plan              TEXT NOT NULL,
    draft_meta        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    approved_at       TEXT,
    sent_at           TEXT,
    viewed_at         TEXT,
    decided_at        TEXT,
    view_count        INTEGER NOT NULL DEFAULT 0,
    send_pending      INTEGER NOT NULL DEFAULT 0,
    booked_option     TEXT,
    booking           TEXT,
    decline_reason    TEXT,
    followup_step     INTEGER NOT NULL DEFAULT 0,
    followup_failures INTEGER NOT NULL DEFAULT 0,
    next_followup_at  TEXT,
    followups_paused  INTEGER NOT NULL DEFAULT 0,
    opted_out         INTEGER NOT NULL DEFAULT 0,
    pending_question  INTEGER NOT NULL DEFAULT 0,
    last_inbound_at   TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'photo',
    filename   TEXT NOT NULL,
    mime       TEXT NOT NULL,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    shared     INTEGER NOT NULL DEFAULT 0,
    caption    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    direction   TEXT NOT NULL,
    channel     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    provider_id TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    data       TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_followup ON cases(status, next_followup_at);
CREATE INDEX IF NOT EXISTS idx_messages_case ON messages(case_id, id);
CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id, id);
CREATE INDEX IF NOT EXISTS idx_attachments_case ON attachments(case_id);
"""

JSON_CASE_FIELDS = ("plan", "draft_meta", "booking")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- generic helpers ---------------------------------------------------

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = self.conn.execute(sql, tuple(params))
        return cur.lastrowid or 0

    def insert(self, table: str, values: dict[str, Any]) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values.values())

    def update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])

    # -- key/value ---------------------------------------------------------

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", [key])
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [key, json.dumps(value, ensure_ascii=False)],
        )

    # -- cases -------------------------------------------------------------

    @staticmethod
    def _decode_case(row: dict | None) -> dict | None:
        if row is None:
            return None
        for field in JSON_CASE_FIELDS:
            row[field] = json.loads(row[field]) if row.get(field) else None
        return row

    def get_case(self, case_id: int) -> dict | None:
        return self._decode_case(self.one("SELECT * FROM cases WHERE id = ?", [case_id]))

    def get_case_by(self, column: str, value: Any) -> dict | None:
        if column not in {"token", "link_code"}:
            raise ValueError(column)
        return self._decode_case(self.one(f"SELECT * FROM cases WHERE {column} = ?", [value]))

    def list_cases(self) -> list[dict]:
        rows = self.query("SELECT * FROM cases ORDER BY updated_at DESC, id DESC")
        return [self._decode_case(r) for r in rows]

    def update_case(self, case_id: int, values: dict[str, Any], *, touch: bool = True) -> None:
        encoded = dict(values)
        for field in JSON_CASE_FIELDS:
            if field in encoded and encoded[field] is not None:
                encoded[field] = json.dumps(encoded[field], ensure_ascii=False)
        if touch:
            encoded["updated_at"] = iso(utcnow())
        self.update("cases", case_id, encoded)

    # -- events / messages ---------------------------------------------------

    def add_event(self, case_id: int, type_: str, data: dict | None = None, at: datetime | None = None) -> None:
        self.insert(
            "events",
            {
                "case_id": case_id,
                "type": type_,
                "data": json.dumps(data, ensure_ascii=False) if data else None,
                "created_at": iso(at or utcnow()),
            },
        )

    def events(self, case_id: int) -> list[dict]:
        rows = self.query("SELECT * FROM events WHERE case_id = ? ORDER BY id", [case_id])
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    def messages(self, case_id: int) -> list[dict]:
        return self.query("SELECT * FROM messages WHERE case_id = ? ORDER BY id", [case_id])

    def attachments(self, case_id: int) -> list[dict]:
        return self.query("SELECT * FROM attachments WHERE case_id = ? ORDER BY id", [case_id])
