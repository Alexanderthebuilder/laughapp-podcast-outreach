"""Phase 6 — secondary sources and verification.

Runs only for restaurants that finished Phase 5 with no named contact or no
email, so it never spends money on a row an earlier free source already filled
(principle 5).

  patterns   generate candidate addresses where a name exists but no email
  verify     MillionVerifier / Bouncer, ~$0.0005 per address
  whois      registrant names on .fi and .ee; doubles as chain detection
  jobads     Duunitori / Oikotie / CV.ee — Finnish hospitality ads conventionally
             close with a named contact, a direct address and a phone

Catch-all domains are common on small restaurant hosting. A verifier returning
accept_all means the guess will deliver but tells us nothing, so it is stored
as catchall_guess and sequenced alongside the general inbox — never counted as
verified.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.crawl import harvest
from lib.db import (add_contact, finish_run, insert_ignore, now,
                    record_failure, start_run)
from lib.emails import is_generic_mailbox
from lib.http import PoliteClient
from lib.patterns import ordered_candidates
from src._cli import base_parser, finish, open_db

PHASE = "phase6"


def _needy(conn, args):
    """Restaurants with no named contact or no email — the only ones worth
    spending a secondary source on."""
    sql = """
      SELECT r.tableonline_id, r.name, r.country, w.domain
      FROM restaurants r
      LEFT JOIN websites w ON w.restaurant_id = r.tableonline_id
      WHERE NOT EXISTS (
              SELECT 1 FROM contacts c WHERE c.restaurant_id = r.tableonline_id
              AND c.email IS NOT NULL AND c.contact_name IS NOT NULL)
      ORDER BY r.tableonline_id
    """
    rows = conn.execute(sql).fetchall()
    return rows[:args.limit] if args.limit else rows


# --------------------------------------------------------------------------
def cmd_patterns(args) -> None:
    """Generate candidate addresses for people we know by name only."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.patterns")
    counts = {"people": 0, "candidates": 0, "skipped_no_domain": 0}

    for row in _needy(conn, args):
        rid, domain = row["tableonline_id"], row["domain"]
        named = conn.execute(
            "SELECT id, contact_name, contact_role FROM contacts"
            " WHERE restaurant_id=? AND contact_name IS NOT NULL AND email IS NULL",
            (rid,)).fetchall()
        if not named:
            continue
        if not domain:
            counts["skipped_no_domain"] += 1
            continue
        known = [r[0] for r in conn.execute(
            "SELECT email FROM contacts WHERE restaurant_id=? AND email IS NOT NULL",
            (rid,)) if r[0] and not is_generic_mailbox(r[0])]

        for person in named:
            cands = ordered_candidates(person["contact_name"], domain, known)
            if not cands:
                continue
            counts["people"] += 1
            # Only the top candidate is stored; verification decides whether to
            # try the next. Storing four guesses per person would inflate the
            # contact table with three addresses that do not exist.
            # add_contact returns False when the guess merged into the
            # existing name-only row for this person — that still produced a
            # candidate address, so it counts either way.
            add_contact(conn, rid, email=cands[0],
                        contact_name=person["contact_name"],
                        contact_role=person["contact_role"],
                        source="pattern_guess", source_url=None,
                        confidence="low", verification_status="unknown")
            counts["candidates"] += 1
        conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    print("run `verify` next — an unverified guess is not a deliverable address")
    finish(conn, args)


# --------------------------------------------------------------------------
def _verify_millionverifier(client, key: str, email: str) -> tuple[str, dict]:
    r = client.get("https://api.millionverifier.com/api/v3/",
                   params={"api": key, "email": email, "timeout": 20})
    if not r.ok:
        return "unknown", {"http": r.status}
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        return "unknown", {"raw": r.text[:200]}
    result = (data.get("result") or "").lower()
    return {"ok": "deliverable", "good": "deliverable",
            "catch_all": "accept_all", "unknown": "unknown",
            "disposable": "undeliverable", "invalid": "undeliverable",
            "bad": "undeliverable"}.get(result, "unknown"), data


def _verify_bouncer(client, key: str, email: str) -> tuple[str, dict]:
    r = client.get("https://api.usebouncer.com/v1.1/email/verify",
                   params={"email": email}, headers={"x-api-key": key})
    if not r.ok:
        return "unknown", {"http": r.status}
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        return "unknown", {"raw": r.text[:200]}
    status = (data.get("status") or "").lower()
    reason = (data.get("reason") or "").lower()
    if status == "deliverable":
        return ("accept_all" if "accept_all" in reason or "catch" in reason
                else "deliverable"), data
    return {"undeliverable": "undeliverable", "risky": "accept_all",
            "unknown": "unknown"}.get(status, "unknown"), data


def cmd_verify(args) -> None:
    conn = open_db(args)
    provider = (os.environ.get("EMAIL_VERIFIER") or "none").strip().lower()
    key = os.environ.get(
        {"millionverifier": "MILLIONVERIFIER_API_KEY",
         "bouncer": "BOUNCER_API_KEY"}.get(provider, ""), "").strip()
    if provider == "none" or not key:
        print("EMAIL_VERIFIER is not configured (set it to millionverifier or "
              "bouncer in .env with the matching key). This phase is deferrable: "
              "everything else runs without it, but the >=85% deliverable-email "
              "criterion cannot be evidenced until it does.", file=sys.stderr)
        raise SystemExit(2)

    run_id = start_run(conn, f"{PHASE}.verify")
    sql = ("SELECT id, restaurant_id, email FROM contacts"
           " WHERE email IS NOT NULL AND (verification_status IS NULL"
           " OR verification_status='unknown') ORDER BY"
           " CASE source WHEN 'pattern_guess' THEN 0 ELSE 1 END, id")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()

    counts = {"checked": 0, "deliverable": 0, "accept_all": 0,
              "undeliverable": 0, "unknown": 0}
    verify = _verify_millionverifier if provider == "millionverifier" else _verify_bouncer

    with PoliteClient(delay=(0.1, 0.3)) as client:
        for row in rows:
            status, _raw = verify(client, key, row["email"])
            counts["checked"] += 1
            counts[status] = counts.get(status, 0) + 1
            confidence = {"deliverable": "verified",
                          "accept_all": "catchall_guess",
                          "undeliverable": "low",
                          "unknown": "unverified"}[status]
            conn.execute(
                "UPDATE contacts SET verification_status=?, verified_at=?,"
                " confidence=? WHERE id=?",
                (status, now(), confidence, row["id"]))
            conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    if counts["accept_all"]:
        print(f"\n{counts['accept_all']} addresses are on catch-all domains: the "
              "guess delivers but gives no signal. Sequence those to both the "
              "guess and the general inbox; do not treat them as verified.")
    finish(conn, args)


# --------------------------------------------------------------------------
def cmd_whois(args) -> None:
    """Registrant names on .fi (Traficom) and .ee.

    Doubles as a second chain-detection signal: one registrant across several
    restaurant domains means a group.
    """
    import shutil
    import subprocess

    conn = open_db(args)
    if not shutil.which("whois"):
        print("whois binary not found — install it (apt install whois) or skip "
              "this source.", file=sys.stderr)
        raise SystemExit(2)
    run_id = start_run(conn, f"{PHASE}.whois")
    counts = {"queried": 0, "registrants": 0, "errors": 0}

    rows = _needy(conn, args)
    name_re = re.compile(
        r"^\s*(?:registrant|registrant name|name|org|organisation|organization|"
        r"holder|registrant organization)\s*[:.]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE)

    for row in rows:
        domain = row["domain"]
        if not domain:
            continue
        counts["queried"] += 1
        try:
            out = subprocess.run(["whois", domain], capture_output=True,
                                 text=True, timeout=30).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            counts["errors"] += 1
            record_failure(conn, PHASE, domain, str(exc), row["tableonline_id"])
            continue
        for m in name_re.finditer(out or ""):
            value = m.group(1).strip()
            if not value or len(value.split()) < 2 or len(value) > 80:
                continue
            if re.search(r"(privacy|redacted|not disclosed|gdpr|whois)", value, re.I):
                continue
            if add_contact(conn, row["tableonline_id"], email=None,
                           contact_name=value, contact_role="domain_registrant",
                           source="whois", source_url=f"whois:{domain}",
                           confidence="low"):
                counts["registrants"] += 1
            break
        conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


# --------------------------------------------------------------------------
# Job boards. Search URLs are configurable because board URL shapes change;
# the harvesting itself reuses the Phase 4 extractor, so a named contact with
# a direct address is picked up wherever on the ad it appears.
JOB_BOARDS = {
    "duunitori": "https://duunitori.fi/tyopaikat?haku={q}",
    "oikotie": "https://tyopaikat.oikotie.fi/haku?text={q}",
    "cv.ee": "https://www.cv.ee/et/search?keyword={q}",
}


def cmd_jobads(args) -> None:
    """Search the job boards by restaurant name and harvest any named contact.

    Finnish hospitality ads conventionally close with a named contact, a direct
    email and a phone — the best single source of a named manager with a direct
    address, and it doubles as a "you're hiring, you're growing" opener.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.jobads")
    counts = {"searched": 0, "ads_found": 0, "contacts": 0, "business_ids": 0}
    boards = args.boards.split(",") if args.boards else list(JOB_BOARDS)

    with PoliteClient() as client:
        for row in _needy(conn, args):
            rid, name, country = row["tableonline_id"], row["name"], row["country"]
            if not name:
                continue
            for board in boards:
                template = JOB_BOARDS.get(board)
                if not template:
                    continue
                # cv.ee is Estonian; the Finnish boards are not worth querying
                # for a Tallinn venue and vice versa.
                if (board == "cv.ee") != (country == "EE"):
                    continue
                url = template.format(q=quote(name))
                counts["searched"] += 1
                r = client.get(url)
                if not r.ok:
                    continue
                result = harvest(r.text, url, country)
                # A hit only counts when the ad actually names this restaurant.
                if name.lower() not in r.text.lower():
                    continue
                counts["ads_found"] += 1
                insert_ignore(conn, "job_ads", {
                    "restaurant_id": rid, "board": board, "url": url,
                    "found_at": now()})
                for e in result["emails"]:
                    if add_contact(conn, rid, email=e["email"],
                                   contact_name=e["contact_name"],
                                   contact_role=e["contact_role"],
                                   source="jobad", source_url=url,
                                   confidence="medium"):
                        counts["contacts"] += 1
                for b in result["business_ids"]:
                    insert_ignore(conn, "business_ids", {
                        "restaurant_id": rid, "business_id": b["business_id"],
                        "country": b["country"], "source_url": url,
                        "confidence": b["confidence"], "method": "jobad",
                        "found_at": now()})
                    counts["business_ids"] += 1
                conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    print("\nNote: board search-result markup changes without notice. If "
          "ads_found is 0 while searched is high, open one of the search URLs "
          "and confirm the result page still renders server-side.")
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("patterns", help="generate candidate addresses from names")
    sub.add_parser("verify", help="verify addresses with the configured provider")
    sub.add_parser("whois", help="domain registrant names")
    j = sub.add_parser("jobads", help="scan job boards for named contacts")
    j.add_argument("--boards", help="comma-separated subset of "
                                    + ",".join(JOB_BOARDS))
    args = p.parse_args(argv)
    {"patterns": cmd_patterns, "verify": cmd_verify, "whois": cmd_whois,
     "jobads": cmd_jobads}[args.cmd](args)


if __name__ == "__main__":
    main()
