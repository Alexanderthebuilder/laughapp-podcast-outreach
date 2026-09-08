"""Phase 7 — scoring and Pipedrive load.

Scoring weights are deliberately explicit and stored per row in
scores.components, so they can be retuned after the first 50 conversations
without re-running any earlier phase.

Pipedrive model:
  * one Organization per restaurant, idempotent on tableonline_id;
  * one Person per *named* contact — general inboxes go on the Organization,
    because inventing a person called "info" poisons a sequence;
  * groups are routed to a separate enterprise track rather than scored on the
    same scale: one company operating four venues is one conversation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.db import finish_run, now, record_failure, start_run, upsert
from lib.emails import is_generic_mailbox
from lib.http import PoliteClient
from lib.paths import EXPORTS
from src._cli import base_parser, finish, open_db, subcommands

PHASE = "phase7"
LABEL = "TableOnline Attack List"

# Tune after the first 50 conversations; the per-row breakdown in
# scores.components makes a retune a re-run of this phase alone.
WEIGHTS = {
    "tenure": 30.0,        # oldest quartile highest — the strongest pitch
    "review_count": 20.0,  # proxy for cover volume
    "named_contact": 25.0, # a named direct contact is worth more than anything
    "verified_email": 10.0,
    "michelin": 8.0,
    "review_score": 7.0,
    "active_offer": 5.0,   # price-sensitive, likely feeling the success fee
    "employer_register": 5.0,   # real payroll, not a holding shell
}

ORG_FIELDS = {
    "tableonline_id": "double", "tableonline_url": "varchar",
    "tenure_bucket": "double", "review_score": "double",
    "review_count": "double", "is_michelin": "varchar",
    "is_group": "varchar", "business_id": "varchar",
    "priority_score": "double", "country": "varchar",
}


# --------------------------------------------------------------------------
def cmd_score(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.score")

    max_reviews = conn.execute(
        "SELECT MAX(review_count) FROM restaurants").fetchone()[0] or 1
    rows = conn.execute("""
        SELECT r.*, m.business_id, m.in_employer_register, m.excluded_reason,
               g.venue_count
        FROM restaurants r
        LEFT JOIN registry_matches m ON m.restaurant_id = r.tableonline_id
        LEFT JOIN groups g ON g.business_id = m.business_id
        ORDER BY r.tableonline_id""").fetchall()
    if args.limit:
        rows = rows[:args.limit]

    counts = {"scored": 0, "enterprise": 0, "excluded": 0}
    for row in rows:
        rid = row["tableonline_id"]
        c: dict[str, float] = {}

        # Bucket 1 (lowest IDs, longest tenure) scores full marks.
        if row["tenure_bucket"]:
            c["tenure"] = WEIGHTS["tenure"] * (5 - row["tenure_bucket"]) / 4

        if row["review_count"]:
            c["review_count"] = WEIGHTS["review_count"] * \
                min(1.0, row["review_count"] / max(max_reviews, 1))

        named = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE restaurant_id=?"
            " AND contact_name IS NOT NULL AND email IS NOT NULL", (rid,)
        ).fetchone()[0]
        if named:
            c["named_contact"] = WEIGHTS["named_contact"]

        verified = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE restaurant_id=?"
            " AND verification_status='deliverable'", (rid,)).fetchone()[0]
        if verified:
            c["verified_email"] = WEIGHTS["verified_email"]

        if row["is_michelin"]:
            c["michelin"] = WEIGHTS["michelin"]
        if row["review_score"]:
            c["review_score"] = WEIGHTS["review_score"] * \
                min(1.0, max(0.0, (row["review_score"] - 3.0) / 2.0))
        if row["has_active_offer"]:
            c["active_offer"] = WEIGHTS["active_offer"]
        if row["in_employer_register"]:
            c["employer_register"] = WEIGHTS["employer_register"]

        # Groups are a different sale; they are tracked separately rather than
        # ranked against single venues.
        track = "enterprise" if (row["venue_count"] or 0) > 1 else "smb"
        if track == "enterprise":
            counts["enterprise"] += 1
        if row["excluded_reason"]:
            counts["excluded"] += 1

        upsert(conn, "scores", {"restaurant_id": rid}, {
            "priority_score": round(sum(c.values()), 2),
            "components": json.dumps(c, ensure_ascii=False),
            "track": track, "scored_at": now()})
        counts["scored"] += 1
    conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    print("\nTop 15 by priority:")
    for r in conn.execute("""
            SELECT r.name, r.city, r.country, s.priority_score, s.track
            FROM scores s JOIN restaurants r ON r.tableonline_id = s.restaurant_id
            ORDER BY s.priority_score DESC LIMIT 15"""):
        print(f"  {r['priority_score']:6.1f}  {r['track']:10s} "
              f"{r['name']} ({r['city'] or '?'}, {r['country'] or '?'})")
    finish(conn, args)


# --------------------------------------------------------------------------
def _outreach_rows(conn, args):
    """Everything ready to load, newest-value first.

    Excludes venues the registers flagged as bankrupt, in liquidation or
    ceased, and venues Google reports permanently closed. Those stay in the
    database as churn intelligence; they simply do not go into outreach.
    """
    sql = """
      SELECT r.*, s.priority_score, s.track, m.business_id, m.company_name,
             m.excluded_reason, g.venue_count, p.business_status, p.website_uri
      FROM restaurants r
      JOIN scores s ON s.restaurant_id = r.tableonline_id
      LEFT JOIN registry_matches m ON m.restaurant_id = r.tableonline_id
      LEFT JOIN groups g ON g.business_id = m.business_id
      LEFT JOIN places p ON p.restaurant_id = r.tableonline_id
      WHERE m.excluded_reason IS NULL
        AND (p.business_status IS NULL OR p.business_status != 'CLOSED_PERMANENTLY')
      ORDER BY s.priority_score DESC
    """
    rows = conn.execute(sql).fetchall()
    return rows[:args.limit] if args.limit else rows


def _org_payload(conn, row, field_keys: dict) -> dict:
    payload = {"name": row["name"]}
    if row["street_address"]:
        payload["address"] = ", ".join(
            p for p in (row["street_address"], row["postal_code"], row["city"]) if p)
    custom = {
        "tableonline_id": row["tableonline_id"],
        "tableonline_url": row["tableonline_url"],
        "tenure_bucket": row["tenure_bucket"],
        "review_score": row["review_score"],
        "review_count": row["review_count"],
        "is_michelin": "yes" if row["is_michelin"] else "no",
        "is_group": "yes" if (row["venue_count"] or 0) > 1 else "no",
        "business_id": row["business_id"],
        "priority_score": row["priority_score"],
        "country": row["country"],
    }
    for our_name, value in custom.items():
        key = field_keys.get(our_name)
        if key and value is not None:
            payload[key] = value
    return payload


def _person_payloads(conn, rid: int, org_id) -> list[dict]:
    """One Person per named contact. Shared inboxes are deliberately absent —
    they belong on the Organization."""
    out = []
    for c in conn.execute(
            "SELECT email, contact_name, contact_role, confidence,"
            " verification_status FROM contacts WHERE restaurant_id=?"
            " AND contact_name IS NOT NULL ORDER BY"
            " CASE confidence WHEN 'verified' THEN 0 WHEN 'high' THEN 1"
            " WHEN 'catchall_guess' THEN 3 ELSE 2 END", (rid,)):
        if c["email"] and is_generic_mailbox(c["email"]):
            continue
        person = {"name": c["contact_name"]}
        if org_id:
            person["org_id"] = org_id
        if c["email"]:
            person["email"] = [{"value": c["email"], "primary": True,
                                "label": "work"}]
        if c["contact_role"]:
            person["job_title"] = c["contact_role"]
        out.append(person)
    return out


def _org_emails(conn, rid: int) -> list[str]:
    """Shared inboxes, which go on the Organization rather than a fake Person."""
    return [r[0] for r in conn.execute(
        "SELECT email FROM contacts WHERE restaurant_id=? AND email IS NOT NULL",
        (rid,)) if r[0] and is_generic_mailbox(r[0])]


def _payload_hash(org: dict, people: list[dict]) -> str:
    blob = json.dumps({"org": org, "people": people}, sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _base_url() -> str:
    domain = (os.environ.get("PIPEDRIVE_COMPANY_DOMAIN") or "").strip()
    if domain:
        domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not domain.endswith(".pipedrive.com"):
            domain += ".pipedrive.com"
        return f"https://{domain}/v1"
    return "https://api.pipedrive.com/v1"


def cmd_setup(args) -> None:
    """Resolve (creating if needed) the Organization custom fields.

    Field keys are generated per account, so they are looked up once and
    cached in pipedrive_fields rather than hardcoded.
    """
    conn = open_db(args)
    token = os.environ.get("PIPEDRIVE_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("PIPEDRIVE_API_TOKEN is not set (Pipedrive personal "
                         "settings -> API). Put it in .env.")
    run_id = start_run(conn, f"{PHASE}.setup")
    base = _base_url()
    counts = {"existing": 0, "created": 0, "failed": 0}

    with PoliteClient(delay=(0.2, 0.4)) as client:
        r = client.get(f"{base}/organizationFields", params={"api_token": token,
                                                            "limit": 500})
        if not r.ok:
            raise SystemExit(f"could not read organizationFields: HTTP {r.status}")
        existing = {f.get("name"): f for f in (json.loads(r.text).get("data") or [])}

        for our_name, field_type in ORG_FIELDS.items():
            match = existing.get(our_name)
            if match:
                counts["existing"] += 1
            else:
                cr = client.request("POST", f"{base}/organizationFields",
                                    params={"api_token": token},
                                    json={"name": our_name, "field_type": field_type})
                if not cr.ok:
                    counts["failed"] += 1
                    record_failure(conn, PHASE, our_name,
                                   f"create field HTTP {cr.status}: {cr.text[:200]}")
                    continue
                match = json.loads(cr.text).get("data") or {}
                counts["created"] += 1
            upsert(conn, "pipedrive_fields",
                   {"entity": "organization", "our_name": our_name},
                   {"field_key": match.get("key"), "field_type": field_type,
                    "options_json": json.dumps(match.get("options") or [],
                                               ensure_ascii=False),
                    "resolved_at": now()})
        conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    print(f"\nMirror the existing Quandoo Attack List structure by applying the "
          f"'{LABEL}' label in Pipedrive so reporting stays consistent.")
    finish(conn, args)


def cmd_push(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.push")
    field_keys = {r["our_name"]: r["field_key"] for r in conn.execute(
        "SELECT our_name, field_key FROM pipedrive_fields WHERE entity='organization'")}
    if not field_keys and not args.dry_run:
        raise SystemExit("no custom fields resolved — run `setup` first")

    token = os.environ.get("PIPEDRIVE_API_TOKEN", "").strip()
    if not token and not args.dry_run:
        raise SystemExit("PIPEDRIVE_API_TOKEN is not set")

    base = _base_url()
    rows = _outreach_rows(conn, args)
    counts = {"organizations": 0, "created": 0, "updated": 0, "unchanged": 0,
              "people": 0, "errors": 0}
    dump = EXPORTS / "pipedrive_payloads.jsonl"
    if args.dry_run:
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text("", encoding="utf-8")

    with PoliteClient(delay=(0.2, 0.4)) as client:
        for row in rows:
            rid = row["tableonline_id"]
            org = _org_payload(conn, row, field_keys)
            sync = conn.execute("SELECT * FROM pipedrive_sync WHERE restaurant_id=?",
                                (rid,)).fetchone()
            people = _person_payloads(conn, rid, sync["org_id"] if sync else None)
            digest = _payload_hash(org, people)

            if sync and sync["payload_hash"] == digest:
                counts["unchanged"] += 1
                continue

            if args.dry_run:
                with open(dump, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "restaurant_id": rid, "track": row["track"],
                        "organization": org, "persons": people,
                        "org_emails": _org_emails(conn, rid),
                        "label": LABEL}, ensure_ascii=False) + "\n")
                counts["organizations"] += 1
                counts["people"] += len(people)
                continue

            org_id = sync["org_id"] if sync else None
            if org_id:
                r = client.request("PUT", f"{base}/organizations/{org_id}",
                                   params={"api_token": token}, json=org)
                action = "updated"
            else:
                r = client.request("POST", f"{base}/organizations",
                                   params={"api_token": token}, json=org)
                action = "created"
            if not r.ok:
                counts["errors"] += 1
                record_failure(conn, PHASE, f"org {rid}",
                               f"HTTP {r.status}: {r.text[:200]}", rid)
                conn.commit()
                continue
            org_id = (json.loads(r.text).get("data") or {}).get("id", org_id)
            counts[action] += 1
            counts["organizations"] += 1

            person_ids = json.loads(sync["person_ids"]) if sync and sync["person_ids"] else {}
            for person in people:
                person["org_id"] = org_id
                key = (person.get("email") or [{}])[0].get("value") or person["name"]
                if key in person_ids:
                    continue
                pr = client.request("POST", f"{base}/persons",
                                    params={"api_token": token}, json=person)
                if not pr.ok:
                    counts["errors"] += 1
                    record_failure(conn, PHASE, f"person {key}",
                                   f"HTTP {pr.status}: {pr.text[:200]}", rid)
                    continue
                person_ids[key] = (json.loads(pr.text).get("data") or {}).get("id")
                counts["people"] += 1

            upsert(conn, "pipedrive_sync", {"restaurant_id": rid}, {
                "org_id": org_id,
                "person_ids": json.dumps(person_ids, ensure_ascii=False),
                "payload_hash": digest, "synced_at": now()})
            conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    if args.dry_run:
        print(f"\ndry run — payloads written to {dump}, nothing sent to Pipedrive")
    finish(conn, args)


def cmd_export(args) -> None:
    """CSV export of the finished list, for review before it reaches the CRM."""
    import csv
    conn = open_db(args)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    path = EXPORTS / "tableonline_attack_list.csv"
    rows = _outreach_rows(conn, args)
    cols = ["tableonline_id", "name", "city", "country", "tableonline_url",
            "tenure_bucket", "review_score", "review_count", "is_michelin",
            "business_id", "company_name", "venue_count", "track",
            "priority_score", "street_address", "postal_code", "phone"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols + ["best_email", "best_contact", "best_role", "email_status"])
        for row in rows:
            best = conn.execute(
                "SELECT email, contact_name, contact_role, verification_status"
                " FROM contacts WHERE restaurant_id=? ORDER BY"
                " CASE confidence WHEN 'verified' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'catchall_guess' THEN 3 ELSE 2 END,"
                " CASE WHEN contact_name IS NOT NULL THEN 0 ELSE 1 END LIMIT 1",
                (row["tableonline_id"],)).fetchone()
            w.writerow([row[c] if c in row.keys() else "" for c in cols] +
                       [best["email"] if best else "",
                        best["contact_name"] if best else "",
                        best["contact_role"] if best else "",
                        best["verification_status"] if best else ""])
    print(f"wrote {path} ({len(rows)} rows)")
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = subcommands(p)
    sub.add_parser("score", help="compute the priority score")
    sub.add_parser("setup", help="resolve/create Pipedrive custom fields")
    pu = sub.add_parser("push", help="upsert organizations and persons")
    pu.add_argument("--dry-run", action="store_true",
                    help="write payloads to exports/ instead of sending")
    sub.add_parser("export", help="CSV export of the finished list")
    args = p.parse_args(argv)
    {"score": cmd_score, "setup": cmd_setup, "push": cmd_push,
     "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    main()
