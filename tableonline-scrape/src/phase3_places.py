"""Phase 3 — Google Places matching.

Places API (New) searchText with a field mask. Not the legacy API.

A wrong website poisons every downstream phase — Phase 4 would crawl a
stranger's site and Phase 5 would join to a stranger's company — so the accept
test is strict and anything short of it becomes needs_review rather than a
guess. Nothing is ever dropped.

Accept when:
  * normalised name similarity >= 0.8, OR
  * the returned location is within 150 m of the listed address.

Coordinates for the restaurant come from JSON-LD (Phase 2). Where we have no
coordinates, street + postcode agreement stands in for the proximity test, and
the match method records which signal was actually used.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.db import finish_run, now, record_failure, start_run, upsert
from lib.http import PoliteClient, store_raw
from lib.normalise import (addresses_agree, haversine_m, name_similarity,
                           parse_street)
from lib.paths import RAW_PLACES
from src._cli import base_parser, finish, open_db

PHASE = "phase3"
ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.websiteUri,"
    "places.internationalPhoneNumber,places.businessStatus,places.rating,"
    "places.userRatingCount,places.location,places.primaryType")

NAME_THRESHOLD = 0.8
DISTANCE_THRESHOLD_M = 150.0

COUNTRY_NAME = {"FI": "Finland", "EE": "Estonia"}


def build_queries(row) -> list[str]:
    """Primary query with the full address, then a name+city fallback."""
    name = (row["name"] or "").strip()
    city = (row["city"] or row["city_slug"] or "").replace("-", " ").strip()
    country = COUNTRY_NAME.get(row["country"], "")
    street = (row["street_address"] or "").strip()
    queries = []
    if name and street:
        queries.append(", ".join(p for p in (name, street, city, country) if p))
    if name:
        fallback = ", ".join(p for p in (name, city, country) if p)
        if fallback not in queries:
            queries.append(fallback)
    return queries


def search_text(client: PoliteClient, api_key: str, query: str,
                country: str | None) -> tuple[int, dict]:
    body: dict = {"textQuery": query, "maxResultCount": 5, "languageCode": "en"}
    if country in ("FI", "EE"):
        body["regionCode"] = country
    r = client.request(
        "POST", ENDPOINT,
        headers={"Content-Type": "application/json",
                 "X-Goog-Api-Key": api_key,
                 "X-Goog-FieldMask": FIELD_MASK},
        json=body)
    try:
        return r.status, (json.loads(r.text) if r.text else {})
    except json.JSONDecodeError:
        return r.status, {"_raw": r.text[:2000], "_error": "non-JSON response"}


def score_candidate(row, place: dict) -> dict:
    """Name similarity and distance for one candidate, plus the accept verdict."""
    display = (place.get("displayName") or {}).get("text") or ""
    sim = name_similarity(row["name"], display)

    loc = place.get("location") or {}
    plat, plng = loc.get("latitude"), loc.get("longitude")
    distance = None
    if row["lat"] is not None and row["lng"] is not None \
            and plat is not None and plng is not None:
        distance = haversine_m(float(row["lat"]), float(row["lng"]),
                               float(plat), float(plng))

    # No coordinates on our side: fall back to street + postcode agreement,
    # which is the same claim ("this is the same address") by other means.
    address_ok = False
    if distance is None and row["street_address"]:
        our_street, our_no = parse_street(row["street_address"])
        their_street, their_no = parse_street(place.get("formattedAddress") or "")
        address_ok = addresses_agree(
            our_street, our_no, row["postal_code"] or "",
            their_street, their_no,
            _postcode_from(place.get("formattedAddress") or ""))

    signals = []
    if sim >= NAME_THRESHOLD:
        signals.append("name")
    if distance is not None and distance <= DISTANCE_THRESHOLD_M:
        signals.append("distance")
    if address_ok:
        signals.append("address")

    return {
        "place": place, "display_name": display, "similarity": sim,
        "distance_m": distance, "accepted": bool(signals),
        "method": "+".join(signals) or "none",
        "lat": plat, "lng": plng,
    }


def _postcode_from(formatted: str) -> str:
    import re
    m = re.search(r"\b(\d{5})\b", formatted or "")
    return m.group(1) if m else ""


def cmd_match(args) -> None:
    conn = open_db(args)
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        print("GOOGLE_PLACES_API_KEY is not set. This is the one hard blocker in "
              "the spec: create a GCP project with billing enabled, enable "
              '"Places API (New)" specifically (not the legacy Places API), '
              "restrict the key to it, and put it in .env.", file=sys.stderr)
        raise SystemExit(2)

    run_id = start_run(conn, f"{PHASE}.match")
    sql = ("SELECT r.* FROM restaurants r"
           " LEFT JOIN places p ON p.restaurant_id = r.tableonline_id"
           " WHERE r.name IS NOT NULL")
    if args.resume:
        sql += " AND p.restaurant_id IS NULL"
    sql += " ORDER BY r.tableonline_id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()

    counts = {"searched": 0, "accepted": 0, "needs_review": 0, "no_results": 0,
              "closed_permanently": 0, "websites": 0, "api_errors": 0,
              "requests": 0}

    with PoliteClient(delay=(0.2, 0.4)) as client:
        for row in rows:
            tid = row["tableonline_id"]
            counts["searched"] += 1
            best = None
            used_query = None

            for query in build_queries(row):
                status, payload = search_text(client, api_key, query, row["country"])
                counts["requests"] += 1
                store_raw(RAW_PLACES / f"{tid}.json",
                          json.dumps({"query": query, "status": status,
                                      "response": payload}, ensure_ascii=False,
                                     indent=2))
                if status != 200:
                    counts["api_errors"] += 1
                    record_failure(conn, PHASE, query,
                                   f"HTTP {status}: {json.dumps(payload)[:300]}", tid)
                    # 400/403 mean the key or the API enablement is wrong; that
                    # is not a per-row problem and burning quota will not fix it.
                    if status in (400, 401, 403):
                        conn.commit()
                        finish_run(conn, run_id, False, counts,
                                   f"aborted: HTTP {status} from Places API — check "
                                   "that the key is valid and 'Places API (New)' "
                                   "is enabled and unrestricted for this API")
                        print(json.dumps(counts, indent=2))
                        raise SystemExit(1)
                    continue

                candidates = [score_candidate(row, p) for p in payload.get("places", [])]
                if not candidates:
                    continue
                # Best = accepted first, then closest name match.
                candidates.sort(key=lambda c: (not c["accepted"], -c["similarity"]))
                best = candidates[0]
                used_query = query
                if best["accepted"]:
                    break

            if best is None:
                counts["no_results"] += 1
                upsert(conn, "places", {"restaurant_id": tid},
                       {"needs_review": 1, "match_method": "none",
                        "query_used": (build_queries(row) or [None])[0],
                        "matched_at": now()})
                conn.commit()
                continue

            p = best["place"]
            status_str = p.get("businessStatus")
            if status_str == "CLOSED_PERMANENTLY":
                counts["closed_permanently"] += 1

            upsert(conn, "places", {"restaurant_id": tid}, {
                "place_id": p.get("id"),
                "display_name": best["display_name"],
                "formatted_address": p.get("formattedAddress"),
                "website_uri": p.get("websiteUri"),
                "international_phone": p.get("internationalPhoneNumber"),
                "business_status": status_str,
                "rating": p.get("rating"),
                "user_rating_count": p.get("userRatingCount"),
                "lat": best["lat"], "lng": best["lng"],
                "primary_type": p.get("primaryType"),
                "name_similarity": round(best["similarity"], 3),
                "distance_m": round(best["distance_m"], 1)
                              if best["distance_m"] is not None else None,
                "match_method": best["method"],
                "query_used": used_query,
                "raw_path": str((RAW_PLACES / f"{tid}.json").relative_to(
                    RAW_PLACES.parent.parent)),
                "matched_at": now(),
            })
            # Written directly: 0 is meaningful and upsert() skips None only.
            conn.execute("UPDATE places SET accepted=?, needs_review=? "
                         "WHERE restaurant_id=?",
                         (1 if best["accepted"] else 0,
                          0 if best["accepted"] else 1, tid))
            if best["accepted"]:
                counts["accepted"] += 1
                if p.get("websiteUri"):
                    counts["websites"] += 1
            else:
                counts["needs_review"] += 1
            conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    print(f"\nEstimated spend this run: ~${counts['requests'] * 0.032:.2f} "
          "(searchText Text Search Pro, indicative)")
    finish(conn, args)


def cmd_seed_websites(args) -> None:
    """Copy accepted Place websites into the websites table for Phase 4.

    CLOSED_PERMANENTLY rows are kept as churn intelligence but never crawled
    or contacted.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.seed_websites")
    from urllib.parse import urlsplit
    rows = conn.execute(
        "SELECT restaurant_id, website_uri, business_status FROM places"
        " WHERE accepted=1 AND website_uri IS NOT NULL").fetchall()
    counts = {"seeded": 0, "skipped_closed": 0, "skipped_bad_url": 0}
    for r in rows:
        if r["business_status"] == "CLOSED_PERMANENTLY":
            counts["skipped_closed"] += 1
            continue
        parts = urlsplit(r["website_uri"])
        host = parts.netloc.lower()
        if not host or parts.scheme not in ("http", "https"):
            counts["skipped_bad_url"] += 1
            continue
        upsert(conn, "websites", {"restaurant_id": r["restaurant_id"]}, {
            "domain": host.removeprefix("www."),
            "base_url": f"{parts.scheme}://{parts.netloc}",
            "status": "pending"})
        counts["seeded"] += 1
    conn.commit()
    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("match", help="searchText against Places API (New)")
    sub.add_parser("seed-websites", help="feed accepted websites into Phase 4")
    args = p.parse_args(argv)
    {"match": cmd_match, "seed-websites": cmd_seed_websites}[args.cmd](args)


if __name__ == "__main__":
    main()
