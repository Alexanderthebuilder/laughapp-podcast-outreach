"""Phase 5 — business register join.

Design principle: this is a primary-key lookup, not a fuzzy-matching problem,
because Phase 4 already harvested the business ID from the restaurant's own
site. Fuzzy matching is only the fallback, and it must agree on two signals
before it is accepted — a meaningful share of Finnish restaurants register at
their accountant's address, so address alone produces confident wrong answers.

Subcommands:
  fi-download   pull the PRH bulk dump (~96 MB zip) once
  fi-load       stream it into companies_fi, keeping only relevant records
  ee-load       load the Estonian bulk files (company details + board members)
  match         join restaurants to both registers
  groups        5c — one company operating four venues is one conversation
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import registry_ee as ee
from lib import registry_fi as fi
from lib.db import (add_contact, finish_run, insert_ignore, now,
                    record_failure, start_run, upsert)
from lib.http import PoliteClient
from lib.normalise import (addresses_agree, name_similarity, normalise_name,
                           normalise_postcode, parse_street)
from lib.paths import RAW_REGISTRY
from src._cli import base_parser, finish, open_db, subcommands

PHASE = "phase5"
NAME_ACCEPT = 0.9          # fallback name match must be strong
NAME_WITH_ADDRESS = 0.75   # lower bar when the address agrees too


# --------------------------------------------------------------------------
def cmd_fi_download(args) -> None:
    """One download, then all matching is local, free and unlimited.

    /companies searches by name, postal code or company type only — there is no
    business-line parameter — so bulk-filtering has to happen here.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.fi_download")
    RAW_REGISTRY.mkdir(parents=True, exist_ok=True)
    dest = RAW_REGISTRY / "all_companies.zip"

    if dest.exists() and args.resume:
        print(f"{dest} already present ({dest.stat().st_size / 1e6:.0f} MB) — "
              "re-run without --resume to refresh")
        finish_run(conn, run_id, True, {"skipped": 1})
        return

    import httpx
    from lib.http import USER_AGENT
    total = 0
    try:
        with httpx.stream("GET", fi.ALL_COMPANIES, timeout=600.0,
                          follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    total += len(chunk)
                    if total % (20 << 20) < (1 << 20):
                        print(f"  {total / 1e6:.0f} MB")
    except Exception as exc:  # noqa: BLE001
        record_failure(conn, PHASE, fi.ALL_COMPANIES, str(exc))
        finish_run(conn, run_id, False, {"bytes": total}, str(exc))
        raise

    print(f"downloaded {total / 1e6:.1f} MB to {dest}")
    finish_run(conn, run_id, True, {"bytes": total})
    finish(conn, args)


def _decompress(zip_path: Path) -> Path:
    """Decompress the bulk zip beside itself, once."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [i for i in zf.infolist()
                   if not i.is_dir() and i.filename.lower().endswith(".json")]
        if not members:
            members = [i for i in zf.infolist() if not i.is_dir()]
        if not members:
            raise SystemExit(f"{zip_path} contains no readable member")
        member = max(members, key=lambda i: i.file_size)
        out = zip_path.with_suffix(".json")
        if out.exists() and out.stat().st_size >= member.file_size:
            return out
        print(f"decompressing {member.filename} ({member.file_size / 1e6:.0f} MB)")
        with zf.open(member) as src, open(out, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
        return out


def cmd_fi_load(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.fi_load")
    src = Path(args.file) if args.file else RAW_REGISTRY / "all_companies.zip"
    if not src.exists():
        raise SystemExit(f"{src} not found — run fi-download first")
    path = _decompress(src) if src.suffix.lower() == ".zip" else src

    # Pre-load what we are looking for, so the stream can discard the rest.
    wanted_ids = {r[0] for r in conn.execute(
        "SELECT business_id FROM business_ids WHERE country='FI'")}
    wanted_names = {normalise_name(r[0]) for r in conn.execute(
        "SELECT name FROM restaurants WHERE country='FI' AND name IS NOT NULL")}
    wanted_names.discard("")
    print(f"looking for {len(wanted_ids)} harvested business IDs and "
          f"{len(wanted_names)} restaurant names")

    counts = {"scanned": 0, "kept": 0, "with_website": 0, "employer": 0}
    for rec in fi.iter_companies(path):
        counts["scanned"] += 1
        norm = fi.normalise_record(rec)
        if norm is None:
            continue
        if not fi.keep_record(norm, wanted_ids, wanted_names):
            continue
        upsert(conn, "companies_fi", {"business_id": norm["business_id"]},
               {k: v for k, v in norm.items() if k != "business_id"})
        counts["kept"] += 1
        if norm["website"]:
            counts["with_website"] += 1
        if norm["in_employer_register"]:
            counts["employer"] += 1
        if counts["kept"] % 2000 == 0:
            conn.commit()
            print(f"  scanned {counts['scanned']}, kept {counts['kept']}")
        if args.limit and counts["kept"] >= args.limit:
            break
    conn.commit()

    # The schema lists `website` but it was absent from live records. Measure
    # the real fill rate rather than designing anything around it.
    fill = counts["with_website"] / counts["kept"] if counts["kept"] else 0.0
    note = (f"website fill rate {fill:.1%} of kept records — "
            + ("usable as an opportunistic join key" if fill > 0.2 else
               "too sparse to build on, as expected"))
    finish_run(conn, run_id, True, counts, note)
    print(json.dumps(counts, indent=2))
    print(note)
    finish(conn, args)


def cmd_ee_load(args) -> None:
    """Load the Estonian bulk files.

    From https://avaandmed.ariregister.rik.ee (downloading open data), take:
      --companies  "Basic data" as CSV — registry code, name, status, address
      --board      "Persons on registry card" as JSON — the board members

    "Persons on registry card" is published only as XML/JSON and nests its
    people inside each company record, which iter_person_rows flattens. Key
    names change between releases, so both files are read by matching field
    names rather than by position or path.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.ee_load")
    counts = {"companies": 0, "board_members": 0, "board_contacts": 0,
              "skipped_rows": 0}

    if args.companies:
        for row in ee.iter_rows(args.companies, ee.COMPANY_COLUMNS):
            norm = ee.normalise_company(row)
            if norm is None:
                counts["skipped_rows"] += 1
                continue
            upsert(conn, "companies_ee", {"registrikood": norm["registrikood"]},
                   {k: v for k, v in norm.items() if k != "registrikood"})
            counts["companies"] += 1
            if counts["companies"] % 5000 == 0:
                conn.commit()
                print(f"  {counts['companies']} companies")
        conn.commit()
        if counts["companies"] == 0:
            print("WARNING: no companies loaded — the file's headers matched "
                  "none of the expected columns. Check the header row against "
                  "COMPANY_COLUMNS in lib/registry_ee.py.", file=sys.stderr)

    if args.board:
        for row in ee.iter_person_rows(args.board):
            norm = ee.normalise_board_member(row)
            if norm is None:
                counts["skipped_rows"] += 1
                continue
            insert_ignore(conn, "company_board_ee", norm)
            counts["board_members"] += 1
            if counts["board_members"] % 5000 == 0:
                conn.commit()
        conn.commit()
        if counts["board_members"] == 0 and args.board:
            print("WARNING: no board members loaded — check the header row "
                  "against BOARD_COLUMNS in lib/registry_ee.py.", file=sys.stderr)

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


# --------------------------------------------------------------------------
def _fi_candidates(conn, row) -> tuple[dict | None, str, float]:
    """(company_row, method, confidence) for one Finnish restaurant."""
    rid = row["tableonline_id"]

    # 1. Direct primary-key lookup on the Y-tunnus harvested in Phase 4.
    for b in conn.execute(
            "SELECT business_id FROM business_ids WHERE restaurant_id=?"
            " AND country='FI' ORDER BY confidence='checksum_valid' DESC", (rid,)):
        c = conn.execute("SELECT * FROM companies_fi WHERE business_id=?",
                         (b[0],)).fetchone()
        if c:
            return dict(c), "business_id", 1.0

    target = normalise_name(row["name"])
    if not target:
        return None, "none", 0.0

    our_street, our_no = parse_street(row["street_address"])
    our_post = normalise_postcode(row["postal_code"])

    # 2/3. Trade names, including parallel and auxiliary names, with the
    # address used to corroborate a weaker name match.
    best, best_method, best_score = None, "none", 0.0
    for c in conn.execute(
            "SELECT * FROM companies_fi WHERE names_norm LIKE ? OR name_norm = ?",
            (f"%{target}%", target)):
        names = [n for n in (c["names_norm"] or "").split("\n") if n]
        score = max((name_similarity(target, n) for n in names), default=0.0)
        addr_ok = addresses_agree(our_street, our_no, our_post,
                                  c["street"] or "", c["building_number"] or "",
                                  c["post_code"] or "")
        if addr_ok and score >= NAME_WITH_ADDRESS:
            method, conf = "name+address", max(score, 0.95)
        elif score >= NAME_ACCEPT:
            method, conf = "name", score
        else:
            continue
        if conf > best_score:
            best, best_method, best_score = dict(c), method, conf

    # 4. Website, opportunistic only — the field is sparsely populated.
    if best is None:
        site = conn.execute("SELECT domain FROM websites WHERE restaurant_id=?",
                            (rid,)).fetchone()
        if site and site["domain"]:
            c = conn.execute(
                "SELECT * FROM companies_fi WHERE website IS NOT NULL"
                " AND website LIKE ?", (f"%{site['domain']}%",)).fetchone()
            if c:
                return dict(c), "website", 0.85
    return best, best_method, best_score


def _ee_candidates(conn, row) -> tuple[dict | None, str, float]:
    rid = row["tableonline_id"]
    for b in conn.execute(
            "SELECT business_id FROM business_ids WHERE restaurant_id=?"
            " AND country='EE'", (rid,)):
        c = conn.execute("SELECT * FROM companies_ee WHERE registrikood=?",
                         (b[0],)).fetchone()
        if c:
            return dict(c), "business_id", 1.0

    target = normalise_name(row["name"])
    if not target:
        return None, "none", 0.0
    our_street, our_no = parse_street(row["street_address"])
    our_post = normalise_postcode(row["postal_code"])

    best, best_method, best_score = None, "none", 0.0
    for c in conn.execute("SELECT * FROM companies_ee WHERE name_norm LIKE ?",
                          (f"%{target}%",)):
        score = name_similarity(target, c["name_norm"])
        their_street, their_no = parse_street(c["street"] or c["address"] or "")
        # Estonian address data is cleaner and companies are more often
        # registered at the venue, so the address fallback is more trustworthy
        # here than in Finland.
        addr_ok = addresses_agree(our_street, our_no, our_post,
                                  their_street, their_no, c["post_code"] or "")
        if addr_ok and score >= NAME_WITH_ADDRESS:
            method, conf = "name+address", max(score, 0.95)
        elif score >= NAME_ACCEPT:
            method, conf = "name", score
        else:
            continue
        if conf > best_score:
            best, best_method, best_score = dict(c), method, conf
    return best, best_method, best_score


def cmd_match(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.match")
    sql = ("SELECT r.* FROM restaurants r"
           " LEFT JOIN registry_matches m ON m.restaurant_id = r.tableonline_id"
           " WHERE r.country IS NOT NULL")
    if args.resume:
        sql += " AND m.restaurant_id IS NULL"
    sql += " ORDER BY r.tableonline_id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    counts = {"matched": 0, "by_business_id": 0, "by_name": 0,
              "by_name_address": 0, "by_website": 0, "needs_review": 0,
              "excluded": 0, "board_contacts": 0}

    for row in conn.execute(sql).fetchall():
        rid = row["tableonline_id"]
        if row["country"] == "FI":
            company, method, conf = _fi_candidates(conn, row)
        else:
            company, method, conf = _ee_candidates(conn, row)

        if company is None:
            counts["needs_review"] += 1
            upsert(conn, "registry_matches", {"restaurant_id": rid},
                   {"country": row["country"], "match_method": "none",
                    "matched_at": now()})
            conn.execute("UPDATE registry_matches SET needs_review=1"
                         " WHERE restaurant_id=?", (rid,))
            conn.commit()
            continue

        counts["matched"] += 1
        counts[{"business_id": "by_business_id", "name": "by_name",
                "name+address": "by_name_address",
                "website": "by_website"}.get(method, "by_name")] += 1

        if row["country"] == "FI":
            excluded = None
            flags = company.get("situation_flags") or ""
            bad = set(flags.split(",")) & fi.EXCLUDE_SITUATIONS
            if bad:
                excluded = ",".join(sorted(bad))
            elif company.get("end_date") or \
                    (company.get("trade_register_status") or "").upper() in \
                    {"2", "CEASED", "LAKANNUT"}:
                excluded = "ceased"
            payload = {
                "country": "FI", "business_id": company["business_id"],
                "company_name": company["primary_name"],
                "match_method": method, "match_confidence": round(conf, 3),
                "tol_code": company["main_business_line"],
                "tol_label": company["main_business_desc"],
                "status": company["trade_register_status"],
                "in_employer_register": company["in_employer_register"],
                "vat_liable": company["vat_liable"],
                "situation_flags": company["situation_flags"],
                "excluded_reason": excluded, "matched_at": now(),
            }
        else:
            excluded = None if ee.is_active(company.get("status")) else "inactive"
            payload = {
                "country": "EE", "business_id": company["registrikood"],
                "company_name": company["name"], "match_method": method,
                "match_confidence": round(conf, 3),
                "tol_code": company.get("emtaks"),
                "status": company.get("status"),
                "excluded_reason": excluded, "matched_at": now(),
            }
            # The board members file is the named-contact unlock for Tallinn
            # and Tartu. These have no email yet — they feed Phase 6 pattern
            # generation.
            for b in conn.execute(
                    "SELECT person_name, role FROM company_board_ee"
                    " WHERE registrikood=?", (company["registrikood"],)):
                if add_contact(conn, rid, email=None, contact_name=b["person_name"],
                               contact_role=b["role"] or "board_member",
                               source="registry_ee", source_url=ee.OPEN_DATA_PAGE,
                               confidence="medium"):
                    counts["board_contacts"] += 1

        if excluded:
            counts["excluded"] += 1
        upsert(conn, "registry_matches", {"restaurant_id": rid}, payload)
        conn.execute("UPDATE registry_matches SET needs_review=0 WHERE restaurant_id=?",
                     (rid,))
        conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def cmd_groups(args) -> None:
    """5c — group detection. One company operating four venues is one
    enterprise conversation, not four cold emails."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.groups")
    conn.execute("DELETE FROM groups")
    rows = conn.execute(
        "SELECT business_id, country, COUNT(*) n,"
        " MIN(company_name) name FROM registry_matches"
        " WHERE business_id IS NOT NULL GROUP BY business_id, country"
        " HAVING n > 1").fetchall()
    for r in rows:
        upsert(conn, "groups", {"business_id": r["business_id"]},
               {"group_parent": r["name"], "venue_count": r["n"],
                "country": r["country"], "detected_at": now()})
    conn.commit()
    counts = {"groups": len(rows),
              "venues_in_groups": sum(r["n"] for r in rows)}
    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    for r in rows[:20]:
        print(f"  {r['name']} ({r['business_id']}): {r['n']} venues")
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = subcommands(p)
    sub.add_parser("fi-download", help="download the PRH bulk dump")
    fl = sub.add_parser("fi-load", help="stream the dump into companies_fi")
    fl.add_argument("--file", help="path to the zip or decompressed JSON")
    el = sub.add_parser("ee-load", help="load Estonian bulk files")
    el.add_argument("--companies", help="ettevotja_rekvisiidid csv/json/zip")
    el.add_argument("--board", help="representation / board members file")
    sub.add_parser("match", help="join restaurants to both registers")
    sub.add_parser("groups", help="5c group detection")
    args = p.parse_args(argv)
    {"fi-download": cmd_fi_download, "fi-load": cmd_fi_load,
     "ee-load": cmd_ee_load, "match": cmd_match, "groups": cmd_groups}[args.cmd](args)


if __name__ == "__main__":
    main()
