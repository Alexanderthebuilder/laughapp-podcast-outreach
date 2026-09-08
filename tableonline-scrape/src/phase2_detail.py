"""Phase 2 — restaurant detail extraction.

Two passes, cheap before expensive (principle 5):

  http    Re-parse the raw pages Phase 1 already stored (fetching only what is
          missing) for the server-injected meta tags. No browser.
  render  Playwright with one shared browser context, ~1.5s/page, for the
          fields that need JS: address, phone, tags, review scores, Michelin.
          JSON-LD is checked before any DOM scraping.

Both passes are resumable and idempotent; --resume skips rows already at the
target status.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import tableonline as to
from lib.cities import city_label
from lib.db import finish_run, now, record_failure, start_run, upsert
from lib.detail_parse import parse_detail
from lib.http import PoliteClient, store_raw
from lib.paths import RAW_PAGES
from src._cli import base_parser, finish, open_db, subcommands

PHASE = "phase2"
RENDER_DELAY_MS = 1500


def _targets(conn, args, statuses: tuple[str, ...]) -> list:
    where: list[str] = []
    params: list = []
    if args.resume:
        marks = ",".join("?" for _ in statuses)
        where.append(f"(phase2_status IS NULL OR phase2_status IN ({marks}))")
        params += list(statuses)
    sql = ("SELECT tableonline_id, name, city_slug, tableonline_url, restaurant_slug"
           " FROM restaurants")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tableonline_id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return conn.execute(sql, params).fetchall()


def cmd_http(args) -> None:
    """Parse the meta tags. Uses the stored raw capture when we have one, so a
    re-run costs no requests at all."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.http")
    rows = _targets(conn, args, ("pending",))
    counts = {"seen": 0, "from_cache": 0, "fetched": 0, "updated": 0, "errors": 0}

    with PoliteClient() as client:
        for row in rows:
            tid = row["tableonline_id"]
            counts["seen"] += 1
            raw = RAW_PAGES / f"{tid}.html"
            if raw.exists():
                html = raw.read_text(encoding="utf-8", errors="replace")
                counts["from_cache"] += 1
            else:
                url = row["tableonline_url"] or to.probe_url(tid)
                r = client.get(url)
                if not r.ok:
                    counts["errors"] += 1
                    record_failure(conn, PHASE, url, r.error or f"HTTP {r.status}", tid)
                    conn.commit()
                    continue
                html = r.text
                store_raw(raw, html)
                counts["fetched"] += 1

            parsed = to.parse_listing(html, tid)
            if not parsed.get("ok"):
                record_failure(conn, PHASE, str(raw),
                               f"unparseable: {parsed.get('reason')}", tid)
                conn.commit()
                continue

            upsert(conn, "restaurants", {"tableonline_id": tid}, {
                "name": parsed["name"],
                "description": parsed["description"],
                "og_image": parsed["og_image"],
                "city": city_label(parsed["city_slug"] or row["city_slug"]),
                "restaurant_slug": parsed["restaurant_slug"],
                "phase2_status": "http_ok",
                "phase2_at": now(),
                "scraped_at": now(),
            })
            counts["updated"] += 1
            conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def cmd_render(args) -> None:
    """Render only what we must. One shared browser context for the whole run."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.render")
    rows = _targets(conn, args, ("pending", "http_ok", "failed"))
    counts = {"seen": 0, "rendered": 0, "ld_json": 0, "errors": 0,
              "with_address": 0, "with_phone": 0}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — run: pip install playwright && "
              "playwright install chromium", file=sys.stderr)
        finish_run(conn, run_id, False, counts, "playwright missing")
        return

    from lib.http import USER_AGENT

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=USER_AGENT, locale="en-GB")
        page = ctx.new_page()
        # Images and fonts are pure cost here — we only need the DOM.
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                   lambda route: route.abort())
        try:
            for row in rows:
                tid = row["tableonline_id"]
                url = row["tableonline_url"] or to.probe_url(tid)
                counts["seen"] += 1
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(RENDER_DELAY_MS)
                    html = page.content()
                except Exception as exc:  # noqa: BLE001 — one bad page must not end the run
                    counts["errors"] += 1
                    record_failure(conn, PHASE, url, str(exc)[:400], tid)
                    conn.execute("UPDATE restaurants SET phase2_status='failed'"
                                 " WHERE tableonline_id=?", (tid,))
                    conn.commit()
                    continue

                store_raw(RAW_PAGES / f"{tid}.rendered.html", html)
                d = parse_detail(html)
                geo = _geo(html)
                if "ld+json" in d["source"]:
                    counts["ld_json"] += 1
                if d["street_address"]:
                    counts["with_address"] += 1
                if d["phone"]:
                    counts["with_phone"] += 1

                upsert(conn, "restaurants", {"tableonline_id": tid}, {
                    "street_address": d["street_address"],
                    "postal_code": d["postal_code"],
                    "city": d["city"] or city_label(row["city_slug"]),
                    "phone": d["phone"],
                    "review_score": d["review_score"],
                    "review_count": d["review_count"],
                    "lat": geo[0], "lng": geo[1],
                    "cuisine_tags": json.dumps(d["cuisine_tags"], ensure_ascii=False)
                                    if d["cuisine_tags"] else None,
                    "atmosphere_tags": json.dumps(d["atmosphere_tags"], ensure_ascii=False)
                                       if d["atmosphere_tags"] else None,
                    # Booleans are meaningful as False, so they are written
                    # directly rather than through upsert's None-skipping.
                    "phase2_status": "rendered",
                    "phase2_at": now(),
                    "scraped_at": now(),
                })
                conn.execute(
                    "UPDATE restaurants SET is_michelin=?, has_active_offer=?,"
                    " accepts_giftcard=? WHERE tableonline_id=?",
                    (d["is_michelin"], d["has_active_offer"], d["accepts_giftcard"], tid))
                counts["rendered"] += 1
                conn.commit()
        finally:
            browser.close()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    if counts["rendered"] and counts["with_address"] / counts["rendered"] < 0.5:
        print("\nWARNING: address fill rate below 50%. The DOM heuristics in "
              "lib/detail_parse.py are structural (tel: hrefs, map links, tag "
              "links) — inspect a stored raw/pages/*.rendered.html and extend "
              "them before the full run.")
    finish(conn, args)


def _geo(html: str) -> tuple[float | None, float | None]:
    """Coordinates from JSON-LD, which is what lets Phase 3 apply the 150 m
    proximity test instead of relying on name similarity alone."""
    from lib import ldjson
    ld = ldjson.extract(html)
    try:
        return float(ld["lat"]), float(ld["lng"])
    except (KeyError, TypeError, ValueError):
        return None, None


def cmd_reparse(args) -> None:
    """Re-run parsing over the stored raw corpus without any network at all.

    Principle 6: raw responses are immutable and parsing is a separate,
    re-runnable step, so improving a selector never costs another crawl.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.reparse")
    counts = {"files": 0, "updated": 0}
    files = sorted(RAW_PAGES.glob("*.rendered.html"))
    if args.limit:
        files = files[:args.limit]
    for f in files:
        try:
            tid = int(f.name.split(".")[0])
        except ValueError:
            continue
        counts["files"] += 1
        raw_html = f.read_text(encoding="utf-8", errors="replace")
        d = parse_detail(raw_html)
        geo = _geo(raw_html)
        upsert(conn, "restaurants", {"tableonline_id": tid}, {
            "lat": geo[0], "lng": geo[1],
            "street_address": d["street_address"], "postal_code": d["postal_code"],
            "city": d["city"], "phone": d["phone"],
            "review_score": d["review_score"], "review_count": d["review_count"],
            "cuisine_tags": json.dumps(d["cuisine_tags"], ensure_ascii=False)
                            if d["cuisine_tags"] else None,
            "atmosphere_tags": json.dumps(d["atmosphere_tags"], ensure_ascii=False)
                               if d["atmosphere_tags"] else None,
        })
        conn.execute("UPDATE restaurants SET is_michelin=?, has_active_offer=?,"
                     " accepts_giftcard=? WHERE tableonline_id=?",
                     (d["is_michelin"], d["has_active_offer"],
                      d["accepts_giftcard"], tid))
        counts["updated"] += 1
    conn.commit()
    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = subcommands(p)
    sub.add_parser("http", help="parse server-injected meta tags (no browser)")
    sub.add_parser("render", help="Playwright pass for JS-only fields")
    sub.add_parser("reparse", help="re-parse the stored raw corpus, no network")
    args = p.parse_args(argv)
    {"http": cmd_http, "render": cmd_render, "reparse": cmd_reparse}[args.cmd](args)


if __name__ == "__main__":
    main()
