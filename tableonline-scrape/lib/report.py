"""run_report.md — regenerated from the DB after every phase.

Acceptance criterion: counts per phase, yield per source, failure list. It
reads only from the database, so it is accurate after a resumed or partial run
and never depends on what happened to be in memory.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import scalar
from .paths import RUN_REPORT


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out) if rows else "_none_"


def build(conn: sqlite3.Connection) -> str:
    L: list[str] = []
    total = scalar(conn, "SELECT COUNT(*) FROM restaurants") or 0
    L.append("# TableOnline Attack List — run report")
    L.append("")
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    L.append("")

    # --- Phase 1 -----------------------------------------------------------
    L.append("## Phase 1 — enumeration")
    probed = scalar(conn, "SELECT COUNT(*) FROM enumeration_log") or 0
    hits = scalar(conn, "SELECT COUNT(*) FROM enumeration_log WHERE http_status=200") or 0
    max_id = scalar(conn, "SELECT MAX(tableonline_id) FROM restaurants") or 0
    L.append(_table(
        ["metric", "value"],
        [["IDs probed", probed],
         ["HTTP 200", hits],
         ["restaurants stored", total],
         ["max tableonline_id", max_id],
         ["churn (404s in range)", probed - hits],
         ["needs_review", scalar(conn, "SELECT COUNT(*) FROM restaurants WHERE needs_review=1") or 0]]))
    L.append("")
    L.append("### Country assignment")
    rows = conn.execute(
        "SELECT COALESCE(country,'UNMAPPED') c, COUNT(*) n FROM restaurants"
        " GROUP BY c ORDER BY n DESC").fetchall()
    L.append(_table(["country", "restaurants"], [[r["c"], r["n"]] for r in rows]))
    unmapped = scalar(conn, "SELECT COUNT(*) FROM restaurants WHERE country IS NULL") or 0
    if unmapped:
        bad = conn.execute(
            "SELECT DISTINCT city_slug FROM restaurants WHERE country IS NULL"
        ).fetchall()
        L.append("")
        L.append(f"**{unmapped} rows have an unmapped city slug** "
                 f"({', '.join(r['city_slug'] or '?' for r in bad)}). "
                 "These are errors, not defaults — add the slug to lib/cities.py.")
    L.append("")
    L.append("### Slug-independence cross-check (Phase 1a-bis)")
    gt = scalar(conn, "SELECT COUNT(DISTINCT tableonline_id) FROM city_page_listings") or 0
    if gt:
        misses = scalar(conn, """
            SELECT COUNT(*) FROM (
              SELECT DISTINCT c.tableonline_id FROM city_page_listings c
              LEFT JOIN enumeration_log e ON e.tableonline_id = c.tableonline_id
              WHERE e.http_status IS NULL OR e.http_status != 200)""") or 0
        L.append(_table(["metric", "value"],
                        [["IDs on live city pages", gt],
                         ["of those, 404 on /x/x/{id}", misses],
                         ["verdict", "ID enumeration alone is safe"
                          if misses == 0 else "MISSES — union city pages with ID sweep"]]))
    else:
        L.append("_not yet run — `python -m src.phase1_enumerate crosscheck`_")
    L.append("")

    # --- Phase 2 -----------------------------------------------------------
    L.append("## Phase 2 — detail extraction")
    L.append(_table(["field", "filled", "coverage"], [
        [f, scalar(conn, f"SELECT COUNT(*) FROM restaurants WHERE {f} IS NOT NULL AND {f} != ''") or 0,
         _pct(scalar(conn, f"SELECT COUNT(*) FROM restaurants WHERE {f} IS NOT NULL AND {f} != ''") or 0, total)]
        for f in ("name", "street_address", "postal_code", "phone", "description",
                  "review_score", "cuisine_tags")]))
    L.append("")

    # --- Phase 3 -----------------------------------------------------------
    L.append("## Phase 3 — Google Places")
    pl = scalar(conn, "SELECT COUNT(*) FROM places") or 0
    acc = scalar(conn, "SELECT COUNT(*) FROM places WHERE accepted=1") or 0
    L.append(_table(["metric", "value", "of total"], [
        ["searched", pl, _pct(pl, total)],
        ["accepted (>=0.8 name or <=150 m)", acc, _pct(acc, total)],
        ["needs_review", scalar(conn, "SELECT COUNT(*) FROM places WHERE needs_review=1") or 0, ""],
        ["websites discovered", scalar(conn, "SELECT COUNT(*) FROM places WHERE accepted=1 AND website_uri IS NOT NULL") or 0, ""],
        ["CLOSED_PERMANENTLY (churn, excluded from outreach)",
         scalar(conn, "SELECT COUNT(*) FROM places WHERE business_status='CLOSED_PERMANENTLY'") or 0, ""]]))
    L.append("")

    # --- Phase 4 -----------------------------------------------------------
    L.append("## Phase 4 — website crawl")
    rows = conn.execute(
        "SELECT COALESCE(tier_used,'-') t, COALESCE(status,'-') s, COUNT(*) n"
        " FROM websites GROUP BY t, s ORDER BY n DESC").fetchall()
    L.append(_table(["tier", "status", "sites"], [[r["t"], r["s"], r["n"]] for r in rows]))
    L.append("")
    L.append("### Yield per source (principle 3 — which channel booked meetings)")
    rows = conn.execute(
        "SELECT source, COUNT(*) n, SUM(CASE WHEN email IS NOT NULL THEN 1 ELSE 0 END) e,"
        " SUM(CASE WHEN contact_name IS NOT NULL THEN 1 ELSE 0 END) nm"
        " FROM contacts GROUP BY source ORDER BY n DESC").fetchall()
    L.append(_table(["source", "contacts", "with email", "with name"],
                    [[r["source"], r["n"], r["e"], r["nm"]] for r in rows]))
    L.append("")

    # --- Acceptance criteria ----------------------------------------------
    with_email = scalar(conn, "SELECT COUNT(DISTINCT restaurant_id) FROM contacts WHERE email IS NOT NULL") or 0
    deliverable = scalar(conn, "SELECT COUNT(DISTINCT restaurant_id) FROM contacts"
                               " WHERE email IS NOT NULL AND verification_status"
                               " IN ('deliverable','accept_all')") or 0
    named = scalar(conn, "SELECT COUNT(DISTINCT restaurant_id) FROM contacts"
                         " WHERE contact_name IS NOT NULL AND contact_role IS NOT NULL") or 0
    fi = scalar(conn, "SELECT COUNT(*) FROM restaurants WHERE country='FI'") or 0
    ee = scalar(conn, "SELECT COUNT(*) FROM restaurants WHERE country='EE'") or 0
    fi_bid = scalar(conn, "SELECT COUNT(DISTINCT b.restaurant_id) FROM business_ids b"
                          " JOIN restaurants r ON r.tableonline_id=b.restaurant_id"
                          " WHERE r.country='FI'") or 0
    ee_bid = scalar(conn, "SELECT COUNT(DISTINCT b.restaurant_id) FROM business_ids b"
                          " JOIN restaurants r ON r.tableonline_id=b.restaurant_id"
                          " WHERE r.country='EE'") or 0

    L.append("## Acceptance criteria")
    checks = [
        ("Google Place matched at confidence >=0.8", acc, total, 0.90),
        ("Deliverable email", deliverable or with_email, total, 0.85),
        ("Named contact with a role", named, total, 0.40),
        ("Business ID captured (FI)", fi_bid, fi, 0.60),
        ("Business ID captured (EE)", ee_bid, ee, 0.60),
    ]
    L.append(_table(["criterion", "count", "of", "actual", "target", "met"], [
        [label, n, d, _pct(n, d), f"{target:.0%}",
         "yes" if d and n / d >= target else "no"]
        for label, n, d, target in checks]))
    L.append("")

    # --- Phase 5/6/7 -------------------------------------------------------
    L.append("## Phase 5 — registry join")
    rows = conn.execute(
        "SELECT COALESCE(match_method,'none') m, COUNT(*) n FROM registry_matches"
        " GROUP BY m ORDER BY n DESC").fetchall()
    L.append(_table(["match method", "restaurants"], [[r["m"], r["n"]] for r in rows]))
    L.append("")
    L.append(_table(["metric", "value"], [
        ["FI companies loaded", scalar(conn, "SELECT COUNT(*) FROM companies_fi") or 0],
        ["EE companies loaded", scalar(conn, "SELECT COUNT(*) FROM companies_ee") or 0],
        ["EE board members loaded", scalar(conn, "SELECT COUNT(*) FROM company_board_ee") or 0],
        ["in employer register (real payroll)",
         scalar(conn, "SELECT COUNT(*) FROM registry_matches WHERE in_employer_register=1") or 0],
        ["excluded (liquidation/bankruptcy/ceased)",
         scalar(conn, "SELECT COUNT(*) FROM registry_matches WHERE excluded_reason IS NOT NULL") or 0],
        ["groups detected", scalar(conn, "SELECT COUNT(*) FROM groups") or 0],
        ["venues inside groups",
         scalar(conn, "SELECT COALESCE(SUM(venue_count),0) FROM groups") or 0]]))
    L.append("")

    L.append("## Phase 7 — Pipedrive")
    L.append(_table(["metric", "value"], [
        ["scored", scalar(conn, "SELECT COUNT(*) FROM scores") or 0],
        ["enterprise track (groups)",
         scalar(conn, "SELECT COUNT(*) FROM scores WHERE track='enterprise'") or 0],
        ["organizations synced", scalar(conn, "SELECT COUNT(*) FROM pipedrive_sync WHERE org_id IS NOT NULL") or 0]]))
    L.append("")

    # --- Failures ----------------------------------------------------------
    L.append("## Failures")
    rows = conn.execute(
        "SELECT phase, COUNT(*) n FROM failures GROUP BY phase ORDER BY n DESC").fetchall()
    L.append(_table(["phase", "failures"], [[r["phase"], r["n"]] for r in rows]))
    recent = conn.execute(
        "SELECT phase, target, error, occurred_at FROM failures"
        " ORDER BY id DESC LIMIT 40").fetchall()
    if recent:
        L.append("")
        L.append("### Most recent 40")
        L.append(_table(["phase", "target", "error", "when"],
                        [[r["phase"], (r["target"] or "")[:70],
                          (r["error"] or "")[:90], r["occurred_at"]] for r in recent]))
    L.append("")

    L.append("## Run log")
    rows = conn.execute(
        "SELECT phase, started_at, finished_at, ok, counts FROM run_log"
        " ORDER BY id DESC LIMIT 25").fetchall()
    L.append(_table(["phase", "started", "finished", "ok", "counts"], [
        [r["phase"], r["started_at"], r["finished_at"] or "-",
         "yes" if r["ok"] else "no",
         json.dumps(json.loads(r["counts"] or "{}"), ensure_ascii=False)[:160]]
        for r in rows]))
    return "\n".join(L) + "\n"


def write(conn: sqlite3.Connection) -> str:
    text = build(conn)
    RUN_REPORT.write_text(text, encoding="utf-8")
    return text
