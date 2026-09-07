"""Contact sheet export — the deliverable.

One row per restaurant with the five things needed to start sending:
restaurant name, contact first name, email, area, phone. A few supporting
columns follow them so a row can be sanity-checked before it is used.

Writes both .xlsx and .csv. No Pipedrive involved.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.cities import city_label
from lib.emails import is_generic_mailbox
from lib.paths import EXPORTS
from src._cli import base_parser, open_db

COUNTRY_LABEL = {"FI": "Finland", "EE": "Estonia"}

HEADERS = [
    # The five columns asked for.
    "Restaurant", "First name", "Email", "Area", "Country", "Phone",
    # Supporting columns, so a row can be checked before it is used.
    "Full name", "Role", "Email type", "Backup email", "Email source",
    "Company", "TableOnline",
]

# Best contact first: a named person beats a shared inbox, and a verified
# address beats a guess. A catch-all guess ranks last — it will deliver but
# nobody confirmed anyone reads it.
CONTACT_ORDER = """
    CASE WHEN contact_name IS NOT NULL THEN 0 ELSE 1 END,
    CASE confidence
        WHEN 'verified' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2
        WHEN 'low' THEN 3 WHEN 'catchall_guess' THEN 4 ELSE 5 END,
    CASE WHEN email IS NOT NULL THEN 0 ELSE 1 END,
    id
"""


def first_name(full: str | None) -> str:
    """The greeting name. "Mari-Liis Tamm" -> "Mari-Liis"."""
    if not full:
        return ""
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    return parts[0] if parts else ""


def _email_type(row) -> str:
    if not row or not row["email"]:
        return ""
    if row["verification_status"] == "accept_all" or row["confidence"] == "catchall_guess":
        return "catch-all (unconfirmed)"
    if row["confidence"] == "verified" or row["verification_status"] == "deliverable":
        return "verified"
    if row["source"] == "pattern_guess":
        return "guessed"
    if is_generic_mailbox(row["email"]):
        return "shared inbox"
    return "personal"


def collect(conn, args) -> list[list]:
    """One row per restaurant that is worth contacting.

    Venues Google reports permanently closed, and companies the registers flag
    as bankrupt, in liquidation or ceased, are left out — they stay in the
    database as churn intelligence but nobody should be emailing them.
    """
    sql = """
      SELECT r.tableonline_id, r.name, r.city, r.city_slug, r.country,
             r.phone AS listed_phone, r.tableonline_url,
             p.international_phone, p.business_status,
             m.company_name, m.excluded_reason
      FROM restaurants r
      LEFT JOIN places p ON p.restaurant_id = r.tableonline_id
      LEFT JOIN registry_matches m ON m.restaurant_id = r.tableonline_id
      WHERE (p.business_status IS NULL OR p.business_status != 'CLOSED_PERMANENTLY')
        AND (m.excluded_reason IS NULL)
      ORDER BY r.country, COALESCE(r.city, r.city_slug), r.name
    """
    out: list[list] = []
    for r in conn.execute(sql).fetchall():
        contact = conn.execute(
            f"SELECT * FROM contacts WHERE restaurant_id=? ORDER BY {CONTACT_ORDER}"
            " LIMIT 1", (r["tableonline_id"],)).fetchone()

        # The best email and the best *name* are not always the same row: a
        # privacy page can name the owner while only the contact page carries a
        # working address. Fall back to any named contact for the greeting.
        named = contact if (contact and contact["contact_name"]) else conn.execute(
            "SELECT * FROM contacts WHERE restaurant_id=? AND contact_name IS NOT NULL"
            f" ORDER BY {CONTACT_ORDER} LIMIT 1", (r["tableonline_id"],)).fetchone()

        email = contact["email"] if contact else None
        if args.only_with_email and not email:
            continue

        # A guessed or catch-all address may bounce. Carry the best confirmed
        # address (usually the shared inbox) so the send has somewhere to fall
        # back to rather than the lead being lost.
        backup = conn.execute(
            "SELECT email FROM contacts WHERE restaurant_id=? AND email IS NOT NULL"
            " AND email != COALESCE(?, '') AND source != 'pattern_guess'"
            " AND confidence != 'catchall_guess'"
            f" ORDER BY {CONTACT_ORDER} LIMIT 1",
            (r["tableonline_id"], email)).fetchone()

        out.append([
            r["name"] or "",
            first_name(named["contact_name"] if named else None),
            email or "",
            r["city"] or city_label(r["city_slug"]),
            COUNTRY_LABEL.get(r["country"], r["country"] or "unknown"),
            r["international_phone"] or r["listed_phone"] or "",
            (named["contact_name"] if named else "") or "",
            (named["contact_role"] if named else "") or "",
            _email_type(contact),
            (backup["email"] if backup else "") or "",
            (contact["source"] if contact else "") or "",
            r["company_name"] or "",
            r["tableonline_url"] or "",
        ])
    return out


def write_csv(rows: list[list], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(HEADERS)
        w.writerows(rows)


def write_xlsx(rows: list[list], path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"

    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F3864")
    body_font = Font(name="Arial", size=11)

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")

    for row in rows:
        ws.append(row)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = body_font
            cell.alignment = Alignment(vertical="center")

    widths = [34, 14, 34, 16, 10, 20, 24, 22, 20, 30, 18, 30, 46]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # The header stays visible and every column is filterable — this sheet is
    # meant to be worked through by area.
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(ws.max_row, 1)}"
    wb.save(path)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    p.add_argument("--only-with-email", action="store_true",
                   help="drop restaurants where no address was found")
    p.add_argument("--name", default="tableonline_contacts",
                   help="output basename under exports/")
    args = p.parse_args(argv)

    conn = open_db(args)
    rows = collect(conn, args)
    if args.limit:
        rows = rows[:args.limit]

    EXPORTS.mkdir(parents=True, exist_ok=True)
    csv_path = EXPORTS / f"{args.name}.csv"
    xlsx_path = EXPORTS / f"{args.name}.xlsx"
    write_csv(rows, csv_path)
    write_xlsx(rows, xlsx_path)

    with_email = sum(1 for r in rows if r[2])
    with_name = sum(1 for r in rows if r[1])
    with_phone = sum(1 for r in rows if r[5])
    print(f"{len(rows)} restaurants")
    if rows:
        print(f"  with email      {with_email:5d}  ({with_email / len(rows):.0%})")
        print(f"  with first name {with_name:5d}  ({with_name / len(rows):.0%})")
        print(f"  with phone      {with_phone:5d}  ({with_phone / len(rows):.0%})")
    print(f"\nwrote {xlsx_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
