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


# Columns added after a database may already exist in the field. CREATE TABLE
# IF NOT EXISTS will not add them, so they are applied explicitly.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "restaurants": [("lat", "REAL"), ("lng", "REAL")],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _ensure_columns(conn)
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

    Two identities can name the same human: an address harvested from a privacy
    page and a bare name from a staff listing. Both are reconciled here, so one
    person never becomes two Pipedrive Persons:

      * an email row matches on the address, case-insensitively;
      * a name-only row matches an existing row with the same normalised name
        and is merged into it rather than inserted;
      * when an email arrives for a person we previously knew only by name, the
        existing row is promoted to that address.
    """
    from .normalise import normalise_name

    email = (email or "").strip().lower() or None
    name_key = "name:" + normalise_name(contact_name) if contact_name else None
    if not email and not name_key:
        return False

    def _find(key):
        return conn.execute(
            "SELECT id, email, dedupe_key FROM contacts"
            " WHERE restaurant_id=? AND dedupe_key=?",
            (restaurant_id, key)).fetchone()

    def _find_by_name():
        """An existing row for the same human, whatever its dedupe_key."""
        if not contact_name:
            return None
        target = normalise_name(contact_name)
        for row in conn.execute(
                "SELECT id, email, contact_name, dedupe_key FROM contacts"
                " WHERE restaurant_id=? AND contact_name IS NOT NULL",
                (restaurant_id,)):
            if normalise_name(row["contact_name"]) == target:
                return row
        return None

    existing = _find(email) if email else None
    promote_to_email = False
    if existing is None:
        existing = _find_by_name() if contact_name else None
        # Only adopt this row for a new address when it has none of its own;
        # two different addresses for one person stay two rows.
        if existing is not None and email:
            if existing["email"]:
                existing = None
            else:
                promote_to_email = True
    if existing is None and not email and name_key:
        existing = _find(name_key)

    if existing is not None:
        # Enrich in place: fill blanks without downgrading what is already there.
        conn.execute(
            "UPDATE contacts SET contact_name=COALESCE(contact_name, ?),"
            " contact_role=COALESCE(contact_role, ?),"
            " email=COALESCE(email, ?) WHERE id=?",
            (contact_name, contact_role, email, existing["id"]))
        if promote_to_email:
            # The channel that produced the deliverable address is the one worth
            # attributing (principle 3), so provenance moves with the promotion.
            conn.execute(
                "UPDATE contacts SET dedupe_key=?, source=?, source_url=?,"
                " confidence=? WHERE id=?",
                (email, source, source_url, confidence, existing["id"]))
        return False

    conn.execute(
        "INSERT INTO contacts (restaurant_id, email, contact_name, contact_role,"
        " source, source_url, confidence, verification_status, found_at, dedupe_key)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (restaurant_id, email, contact_name, contact_role, source, source_url,
         confidence, verification_status, now(), email or name_key))
    return True


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row else None
