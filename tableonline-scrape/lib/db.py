"""SQLite access. One connection helper, plus the idempotent upserts every
phase needs.

Principle 1: no in-memory state — a phase writes each row as it completes it,
so a crashed run resumes instead of restarting.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .paths import DB_PATH, ROOT, ensure_dirs

SCHEMA = ROOT / "db" / "schema.sql"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(path or DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def upsert(conn: sqlite3.Connection, table: str, keys: dict, values: dict) -> None:
    """INSERT .. ON CONFLICT UPDATE on the given natural key.

    Only non-None values overwrite, so a later cheap phase never blanks a field
    an earlier expensive phase filled (principle 5, in reverse).
    """
    row = {**keys, **{k: v for k, v in values.items() if v is not None}}
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conflict = ", ".join(keys)
    updates = ", ".join(f"{k}=excluded.{k}" for k in row if k not in keys)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({marks})"
    if updates:
        sql += f" ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    else:
        sql += f" ON CONFLICT({conflict}) DO NOTHING"
    conn.execute(sql, list(row.values()))


def insert_ignore(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})",
                 list(row.values()))


def record_failure(conn: sqlite3.Connection, phase: str, target: str, error: str,
                   restaurant_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO failures (phase, restaurant_id, target, error, occurred_at)"
        " VALUES (?,?,?,?,?)",
        (phase, restaurant_id, target, str(error)[:1000], now()),
    )


def start_run(conn: sqlite3.Connection, phase: str) -> int:
    cur = conn.execute(
        "INSERT INTO run_log (phase, started_at, ok) VALUES (?,?,0)", (phase, now()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, ok: bool,
               counts: dict | None = None, notes: str = "") -> None:
    conn.execute(
        "UPDATE run_log SET finished_at=?, ok=?, counts=?, notes=? WHERE id=?",
        (now(), 1 if ok else 0, json.dumps(counts or {}, ensure_ascii=False),
         notes, run_id))
    conn.commit()


def add_contact(conn: sqlite3.Connection, restaurant_id: int, *, email: str | None,
                contact_name: str | None, contact_role: str | None, source: str,
                source_url: str | None, confidence: str,
                verification_status: str | None = None) -> bool:
    """Idempotent contact insert. Returns True when a new row was created.

    dedupe_key is the email when we have one, else name:<normalised>, so
    Estonian board members (name, no email yet) dedupe on the person.
    """
    from .normalise import normalise_name

    email = (email or "").strip().lower() or None
    if email:
        key = email
    elif contact_name:
        key = "name:" + normalise_name(contact_name)
    else:
        return False
    cur = conn.execute("SELECT id FROM contacts WHERE restaurant_id=? AND dedupe_key=?",
                       (restaurant_id, key))
    existing = cur.fetchone()
    if existing:
        # Enrich in place: fill blanks without downgrading what is already there.
        conn.execute(
            "UPDATE contacts SET contact_name=COALESCE(contact_name, ?),"
            " contact_role=COALESCE(contact_role, ?),"
            " email=COALESCE(email, ?) WHERE id=?",
            (contact_name, contact_role, email, existing["id"]))
        return False
    conn.execute(
        "INSERT INTO contacts (restaurant_id, email, contact_name, contact_role,"
        " source, source_url, confidence, verification_status, found_at, dedupe_key)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (restaurant_id, email, contact_name, contact_role, source, source_url,
         confidence, verification_status, now(), key))
    return True


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row else None
