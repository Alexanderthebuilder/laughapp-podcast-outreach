"""Shared CLI harness. Every phase takes --limit and --resume so it can be
proven on 20 rows before running full-scale."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import report  # noqa: E402
from lib.db import connect, init_db  # noqa: E402
from lib.paths import DB_PATH, ensure_dirs  # noqa: E402


def load_env() -> None:
    """Read .env if present. Keys never live in source (Phase 0)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        load_dotenv(env)


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after N rows — prove the phase on 20 first")
    p.add_argument("--resume", action="store_true",
                   help="skip rows this phase has already completed")
    p.add_argument("--no-report", action="store_true",
                   help="skip regenerating run_report.md")
    return p


def common_parent() -> argparse.ArgumentParser:
    """The shared flags again, for attaching to each subcommand.

    Defined only on the top-level parser, argparse accepts them solely *before*
    the subcommand, so the natural `sweep --limit 20` is rejected. Repeating
    them on every subparser makes both orders work. The defaults are SUPPRESS
    so an unused subcommand flag does not overwrite a value given at the top
    level — with a real default it would silently reset it.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--db", default=argparse.SUPPRESS, help="SQLite path")
    p.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                   help="stop after N rows")
    p.add_argument("--resume", action="store_true", default=argparse.SUPPRESS,
                   help="skip rows this phase has already completed")
    p.add_argument("--no-report", action="store_true", default=argparse.SUPPRESS,
                   help="skip regenerating run_report.md")
    return p


def subcommands(parser: argparse.ArgumentParser):
    """Subparser factory that attaches the shared flags to every subcommand."""
    sub = parser.add_subparsers(dest="cmd", required=True)
    original = sub.add_parser

    def add_parser(name, **kw):
        kw.setdefault("parents", [])
        kw["parents"] = list(kw["parents"]) + [common_parent()]
        return original(name, **kw)

    sub.add_parser = add_parser
    return sub


def open_db(args) -> sqlite3.Connection:
    ensure_dirs()
    load_env()
    conn = connect(args.db)
    init_db(conn)
    return conn


def finish(conn: sqlite3.Connection, args) -> None:
    conn.commit()
    if not getattr(args, "no_report", False):
        report.write(conn)
        print("run_report.md updated")
