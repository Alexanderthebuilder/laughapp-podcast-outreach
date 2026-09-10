"""Validate harvested contact names, and strip the ones that are not people.

A capitalised link label looks exactly like a surname to a pattern matcher, so
"Facebook", "Gift Cards" and "Happy Hour" can reach the First name column. Three
independent signals catch them:

  1. Vocabulary — a candidate containing a known non-name word is not a person.
  2. Frequency  — a real person works at one restaurant. A name appearing at
                  several distinct venues is site furniture, and this catches
                  words no list anticipated.
  3. Self-reference — a "name" that repeats the restaurant's own name, or a
                  word from its domain, is a heading rather than a human.

Nothing is destroyed: only contact_name and contact_role are cleared, the email
stays, and every removal is written to exports/ for review. The raw pages are
still on disk, so `phase4 reparse` can rebuild names from scratch either way.

  python -m src.clean_names                 # report, changes nothing
  python -m src.clean_names --list-all      # every distinct name, by frequency
  python -m src.clean_names --apply         # clear the flagged names
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.emails import _looks_like_person
from lib.normalise import normalise_name, strip_diacritics
from lib.paths import EXPORTS
from src._cli import base_parser, open_db

# A person works at one restaurant. Two is possible (a small group); three or
# more distinct venues means the string came from a template, not a payroll.
DEFAULT_MIN_SITES = 3


def _domain_tokens(domain: str | None) -> set[str]:
    if not domain:
        return set()
    stem = strip_diacritics(domain).lower()
    for suffix in (".fi", ".ee", ".com", ".net", ".org", ".eu", ".se"):
        stem = stem.removesuffix(suffix)
    return {t for t in stem.replace("-", " ").replace(".", " ").split() if len(t) > 3}


def gather(conn):
    """Every named contact with the context needed to judge it."""
    return conn.execute("""
        SELECT c.id, c.contact_name, c.contact_role, c.email, c.source,
               c.restaurant_id, r.name AS restaurant, w.domain
        FROM contacts c
        JOIN restaurants r ON r.tableonline_id = c.restaurant_id
        LEFT JOIN websites w ON w.restaurant_id = c.restaurant_id
        WHERE c.contact_name IS NOT NULL
        ORDER BY c.id""").fetchall()


def analyse(rows, min_sites: int) -> tuple[dict, dict]:
    """Return (reasons_by_row_id, sites_by_normalised_name)."""
    sites: dict[str, set[int]] = {}
    for r in rows:
        sites.setdefault(normalise_name(r["contact_name"]), set()).add(
            r["restaurant_id"])

    reasons: dict[int, list[str]] = {}
    for r in rows:
        name = r["contact_name"]
        key = normalise_name(name)
        why: list[str] = []

        if not _looks_like_person(name):
            why.append("not a person-shaped name")

        n_sites = len(sites.get(key, ()))
        if n_sites >= min_sites:
            why.append(f"appears at {n_sites} different restaurants")

        # A heading that repeats the venue, or a word lifted from its domain.
        venue = normalise_name(r["restaurant"] or "")
        if venue and key and (key == venue or key in venue or venue in key):
            why.append("repeats the restaurant's own name")
        tokens = {strip_diacritics(t).lower() for t in (key or "").split()}
        if tokens & _domain_tokens(r["domain"]):
            why.append("contains a word from the site's domain")

        if why:
            reasons[r["id"]] = why
    return reasons, sites


def main(argv=None) -> None:
    p = base_parser(__doc__)
    p.add_argument("--apply", action="store_true",
                   help="clear the flagged names (emails are kept)")
    p.add_argument("--min-sites", type=int, default=DEFAULT_MIN_SITES,
                   help=f"flag a name seen at this many venues (default "
                        f"{DEFAULT_MIN_SITES})")
    p.add_argument("--list-all", action="store_true",
                   help="print every distinct name with its frequency")
    args = p.parse_args(argv)

    conn = open_db(args)
    rows = gather(conn)
    reasons, sites = analyse(rows, args.min_sites)

    if args.list_all:
        print(f"{'name':34s} {'venues':>6s}  status")
        print("-" * 62)
        seen: set[str] = set()
        for r in sorted(rows, key=lambda r: (-len(sites[normalise_name(r["contact_name"])]),
                                             r["contact_name"] or "")):
            key = normalise_name(r["contact_name"])
            if key in seen:
                continue
            seen.add(key)
            mark = "REJECT" if r["id"] in reasons else "keep"
            print(f"{(r['contact_name'] or '')[:34]:34s} "
                  f"{len(sites[key]):>6d}  {mark}")
        print()

    flagged = [r for r in rows if r["id"] in reasons]
    print(f"{len(rows)} named contacts, {len(flagged)} flagged as not a person")
    if flagged:
        print(f"\n{'name':30s} {'restaurant':26s} why")
        print("-" * 100)
        shown: set[str] = set()
        for r in flagged:
            key = normalise_name(r["contact_name"])
            if key in shown:
                continue
            shown.add(key)
            print(f"{(r['contact_name'] or '')[:30]:30s} "
                  f"{(r['restaurant'] or '')[:26]:26s} "
                  f"{'; '.join(reasons[r['id']])}")
        print(f"\n({len(shown)} distinct names above, {len(flagged)} rows)")

    EXPORTS.mkdir(parents=True, exist_ok=True)
    audit = EXPORTS / "rejected_names.csv"
    with open(audit, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["contact_id", "name", "role", "email", "restaurant",
                    "source", "reasons"])
        for r in flagged:
            w.writerow([r["id"], r["contact_name"], r["contact_role"], r["email"],
                        r["restaurant"], r["source"], "; ".join(reasons[r["id"]])])
    print(f"\nfull list written to {audit}")

    if not args.apply:
        print("\nNothing changed. Re-run with --apply to clear these names "
              "(emails are kept, and only the name and role are cleared).")
        return

    for r in flagged:
        conn.execute("UPDATE contacts SET contact_name=NULL, contact_role=NULL"
                     " WHERE id=?", (r["id"],))
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE contact_name IS NOT NULL").fetchone()[0]
    print(f"\ncleared {len(flagged)} names. {remaining} named contacts remain.")
    print("Re-run `python -m src.export_sheet` to rebuild the sheet.")


if __name__ == "__main__":
    main()
