"""Where the run currently stands, in one screen.

Sessions time out, terminals lose scrollback, and a phase that half-finished
looks identical to one that never ran. This answers "where are we" without
having to re-derive it from six separate queries.

  python -m src.status
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src._cli import base_parser, open_db


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "    - "


def _one(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def report(conn) -> None:
    restaurants = _one(conn, "SELECT COUNT(*) FROM restaurants")
    if not restaurants:
        print("No restaurants yet — Phase 1 has not run.")
        return

    print(f"RESTAURANTS  {restaurants}")
    for country, n in conn.execute(
            "SELECT COALESCE(country, 'unmapped'), COUNT(*) FROM restaurants"
            " GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {country:10s} {n:5d}  {_pct(n, restaurants)}")

    print("\nCOVERAGE")
    for label, sql in [
            ("phone", "SELECT COUNT(*) FROM restaurants WHERE phone IS NOT NULL"),
            ("address", "SELECT COUNT(*) FROM restaurants"
                        " WHERE street_address IS NOT NULL"),
            ("website", "SELECT COUNT(*) FROM websites WHERE base_url IS NOT NULL"),
            ("crawled", "SELECT COUNT(*) FROM websites WHERE status='ok'"),
            ("any email", "SELECT COUNT(DISTINCT restaurant_id) FROM contacts"
                          " WHERE email IS NOT NULL"),
            ("named contact", "SELECT COUNT(DISTINCT restaurant_id) FROM contacts"
                              " WHERE contact_name IS NOT NULL")]:
        n = _one(conn, sql)
        print(f"  {label:14s} {n:5d}  {_pct(n, restaurants)}")

    contacts = _one(conn, "SELECT COUNT(*) FROM contacts")
    named = _one(conn, "SELECT COUNT(*) FROM contacts WHERE contact_name IS NOT NULL")
    print(f"\nCONTACTS  {contacts} rows, {named} carrying a name")
    for source, n in conn.execute(
            "SELECT source, COUNT(*) FROM contacts GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {source:20s} {n:5d}")

    # The judging pass. "unjudged" is the number that matters: clearing a
    # restaurant's best contact promotes the next one, so a name that never
    # reached the sheet can still surface later.
    judged = _one(conn, "SELECT COUNT(*) FROM name_verdicts")
    people = _one(conn, "SELECT COUNT(*) FROM name_verdicts WHERE is_person=1")
    unjudged = _one(conn, """
        SELECT COUNT(DISTINCT c.contact_name) FROM contacts c
        LEFT JOIN name_verdicts v ON v.name = c.contact_name
        WHERE c.contact_name IS NOT NULL AND v.name_norm IS NULL""")
    print(f"\nNAME JUDGING  {judged} verdicts cached "
          f"({people} people, {judged - people} not)")
    if unjudged:
        print(f"  {unjudged} distinct names still unjudged — run "
              f"`python -m src.ai_review_names --apply`")
    else:
        print("  nothing left unjudged")

    ids = _one(conn, "SELECT COUNT(*) FROM restaurants WHERE business_id IS NOT NULL") \
        if _has_column(conn, "restaurants", "business_id") else 0
    if ids:
        print(f"\nREGISTRY  {ids} business IDs resolved")
    ee = _one(conn, "SELECT COUNT(*) FROM contacts WHERE source='registry_ee'")
    print(f"  {ee} Estonian board members loaded"
          + ("" if ee else "  — run `python -m src.phase5_registry ee-load`"))


def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def main(argv=None) -> None:
    p = base_parser(__doc__)
    args = p.parse_args(argv)
    report(open_db(args))


if __name__ == "__main__":
    main()
