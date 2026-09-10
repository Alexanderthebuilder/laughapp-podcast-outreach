"""Where the run currently stands, in one screen.

Sessions time out, terminals lose scrollback, and a phase that half-finished
looks identical to one that never ran. This answers "where are we" without
having to re-derive it from six separate queries.

  python -m src.status
  python -m src.status --domains       # off-domain addresses that reach the
                                       # sheet, grouped by domain
  python -m src.status --domains --all # every one of them, one per line
  python -m src.status --domains --every-contact   # not just the sheet's
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
            # The phone that reaches the sheet, which prefers the Google
            # Places number over the one on the listing. Counting
            # restaurants.phone alone reported 0.5% while the sheet carried
            # 96%, which reads as catastrophe rather than as a bad metric.
            ("phone", "SELECT COUNT(*) FROM restaurants r"
                      " LEFT JOIN places p ON p.restaurant_id = r.tableonline_id"
                      " WHERE COALESCE(p.international_phone, r.phone) IS NOT NULL"),
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
    # Registry-sourced names are never judged, so counting them here would
    # report a backlog that no command can clear.
    unjudged = _one(conn, """
        SELECT COUNT(*) FROM (
          SELECT c.contact_name FROM contacts c
          LEFT JOIN name_verdicts v ON v.name = c.contact_name
          WHERE c.contact_name IS NOT NULL AND v.name_norm IS NULL
          GROUP BY LOWER(c.contact_name)
          HAVING SUM(CASE WHEN c.source IN ('registry_fi','registry_ee')
                          THEN 0 ELSE 1 END) > 0)""")
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

    # Estonia loads in two steps and either can silently produce nothing:
    # the register file may parse to no rows, or the rows may fail to match a
    # restaurant. One number cannot tell those apart.
    companies = _one(conn, "SELECT COUNT(*) FROM companies_ee")
    board = _one(conn, "SELECT COUNT(*) FROM company_board_ee")
    if companies or board:
        matched = _one(conn, "SELECT COUNT(*) FROM registry_matches"
                             " WHERE business_id IN"
                             " (SELECT registrikood FROM companies_ee)") \
            if _has_column(conn, "companies_ee", "registrikood") else 0
        print(f"  {companies} EE companies, {board} board rows, "
              f"{matched} matched to a restaurant")

    stray = len(off_domain(conn))
    if stray:
        print(f"\nOFF-DOMAIN  {stray} of the addresses the sheet would send to "
              f"are not on")
        print("  the restaurant's own site. Some are legitimate (group "
              "domains, an owner's")
        print("  Gmail); some are the wrong company. "
              "`python -m src.status --domains`.")


# Mirrors src/export_sheet.CONTACT_ORDER. The sheet sends to one address per
# restaurant, so that is the address worth checking; the rest of the contacts
# table is never mailed and reviewing it is 800 rows of nothing.
_SHEET_PICK = """
    c.id = (SELECT c2.id FROM contacts c2
             WHERE c2.restaurant_id = c.restaurant_id AND c2.email IS NOT NULL
             ORDER BY CASE WHEN c2.contact_name IS NOT NULL THEN 0 ELSE 1 END,
                      CASE c2.confidence
                        WHEN 'verified' THEN 0 WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2 WHEN 'low' THEN 3
                        WHEN 'catchall_guess' THEN 4 ELSE 5 END,
                      CASE WHEN c2.email IS NOT NULL THEN 0 ELSE 1 END,
                      c2.id
             LIMIT 1)
"""

OFF_DOMAIN_SQL = """
    SELECT r.name AS restaurant, w.domain AS site,
           c.contact_name, c.email, c.source
      FROM contacts c
      JOIN restaurants r ON r.tableonline_id = c.restaurant_id
      JOIN websites   w ON w.restaurant_id  = c.restaurant_id
     WHERE c.email IS NOT NULL AND w.domain IS NOT NULL
       AND LOWER(SUBSTR(c.email, INSTR(c.email, '@') + 1)) <> LOWER(w.domain)
       AND LOWER(SUBSTR(c.email, INSTR(c.email, '@') + 1))
           NOT LIKE '%.' || LOWER(w.domain)
       {scope}
     ORDER BY r.name
"""


def off_domain(conn, every_contact: bool = False) -> list:
    """Contacts whose address is not on the restaurant's own website domain.

    A real person can still be the wrong person. Three teachers at
    @edu.hel.fi were harvested onto a cooking-school restaurant: correctly
    judged as people, useless as leads, and invisible to a name filter
    because nothing is wrong with the names. The domain is the tell.

    Not every mismatch is bad — a group restaurant legitimately uses the
    parent company's domain, and a Gmail address is often the actual owner —
    so this reports rather than deletes.

    By default only the address the sheet would send to, because that is the
    only one anybody receives.
    """
    scope = "" if every_contact else f"AND {_SHEET_PICK}"
    return conn.execute(OFF_DOMAIN_SQL.format(scope=scope)).fetchall()


def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def main(argv=None) -> None:
    p = base_parser(__doc__)
    p.add_argument("--domains", action="store_true",
                   help="report contacts whose email domain is not the "
                        "restaurant's own website domain")
    p.add_argument("--all", action="store_true",
                   help="with --domains, print every row instead of a summary")
    p.add_argument("--every-contact", action="store_true",
                   help="with --domains, check every contact rather than only "
                        "the address the sheet would send to")
    args = p.parse_args(argv)
    conn = open_db(args)
    if args.domains:
        rows = off_domain(conn, every_contact=args.every_contact)
        if args.all:
            for row in rows:
                print(f"{(row['restaurant'] or '')[:26]:26s} "
                      f"site={row['site'][:22]:22s} "
                      f"{(row['contact_name'] or '-')[:20]:20s} {row['email']}")
            return
        # Grouped, because 761 lines is not reviewable and the shape of the
        # problem is in the repeats: one domain appearing 40 times is a
        # systematic harvest off the wrong site, while a long tail of
        # single-restaurant Gmail addresses is mostly real owners.
        counts: dict[str, int] = {}
        venues: dict[str, set] = {}
        for row in rows:
            dom = row["email"].split("@")[-1].lower()
            counts[dom] = counts.get(dom, 0) + 1
            venues.setdefault(dom, set()).add(row["restaurant"])
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        scope = "contacts" if args.every_contact else "sheet addresses"
        print(f"{len(rows)} off-domain {scope} across {len(counts)} domains\n")
        for dom, n in ranked[:40]:
            print(f"  {n:4d}  {dom:34s} {len(venues[dom])} restaurant(s)")
        tail = sum(n for _, n in ranked[40:])
        if tail:
            print(f"  {tail:4d}  (in {len(ranked) - 40} further domains)")
        print("\nA domain on many restaurants is a harvest off the wrong site.")
        print("A domain on one restaurant is usually that owner's real address.")
        return
    report(conn)


if __name__ == "__main__":
    main()
