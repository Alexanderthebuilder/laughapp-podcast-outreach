"""Phase 1 — enumeration.

The site routes on the trailing ID and ignores the slug for active listings,
so a sweep of /en/x/x/{id} returns exactly the active restaurants and
self-filters the churned ones. Every 200 yields the true city and restaurant
slug via canonical, plus name and description via og:*.

Subcommands:
  discover    robots.txt, sitemap, city selector, JSON endpoints -> docs/discovery.md
  sweep       the ID sweep itself
  crosscheck  Phase 1a-bis — rule out silent under-collection before trusting the rule
  coverage    Phase 1c — confirm uncollected IDs in range genuinely 404
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import consent
from lib import tableonline as to
from lib.cities import (city_label, country_for_slug, load_known_slugs,
                        record_slug)
from lib.db import finish_run, now, record_failure, start_run, upsert
from lib.http import PoliteClient, store_raw
from lib.paths import DOCS, RAW_PAGES
from src._cli import base_parser, finish, open_db, subcommands

PHASE = "phase1"
DEFAULT_MAX_ID = 2500
# Fewer than this means the listing pages did not render at all.
MIN_GROUND_TRUTH = 25

# The listing pages paginate, so one city yields only its first screen —
# roughly 20 restaurants. Breadth across cities is a cheaper way to a
# meaningful ground-truth set than defeating the pagination.
DEFAULT_CROSSCHECK_CITIES = (
    "helsinki", "tallinn", "tampere", "turku", "espoo", "vantaa", "oulu",
    "tartu", "jyvaskyla", "lahti", "parnu", "kuopio", "porvoo", "rovaniemi",
)


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------
def cmd_discover(args) -> None:
    """Fallbacks 1-3 of Phase 1b, plus the city selector the country mapping
    is built from. Findings are written to docs/discovery.md before any
    scraper code runs against them."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.discover")
    findings: dict = {}

    with PoliteClient() as client:
        r = client.get(f"{to.BASE}/robots.txt")
        findings["robots"] = {"status": r.status, "body": r.text[:4000]}
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text or "")

        for path in ["/sitemap.xml", "/sitemap_index.xml"] + sitemaps:
            url = path if path.startswith("http") else f"{to.BASE}{path}"
            sr = client.get(url)
            findings.setdefault("sitemaps", []).append(
                {"url": url, "status": sr.status, "bytes": len(sr.text or "")})
            if sr.ok and "<loc" in (sr.text or ""):
                from lib.cms_detect import parse_sitemap
                locs = parse_sitemap(sr.text, limit=5000)
                findings["sitemap_urls"] = len(locs)
                findings["sitemap_sample"] = locs[:20]
                store_raw(RAW_PAGES.parent / "discovery" / "sitemap.xml", sr.text)

        # City selector: the country mapping must come from the site, not a
        # hardcoded list.
        home = client.get(f"{to.BASE}/en/helsinki")
        if home.ok:
            store_raw(RAW_PAGES.parent / "discovery" / "helsinki.html", home.text)
            slugs = sorted(set(re.findall(r'href=["\']/en/([a-z0-9\-]+)/?["\']',
                                          home.text or "")))
            skip = {"about", "search", "login", "signup", "terms", "privacy",
                    "contact", "help", "gift", "giftcard", "book", "x"}
            city_slugs = [s for s in slugs if s not in skip]
            findings["city_selector_slugs"] = city_slugs
            for s in city_slugs:
                country, source = country_for_slug(s)
                record_slug(conn, s, None, country, "city_selector")
                if country is None:
                    findings.setdefault("unmapped_slugs", []).append(s)

            # Fallback 3: grep the bundle for an API surface worth using instead.
            bundles = re.findall(r'src=["\']([^"\']+\.js)["\']', home.text or "")
            api_hits: list[dict] = []
            for b in bundles[:6]:
                burl = b if b.startswith("http") else f"{to.BASE}{b}"
                br = client.get(burl)
                if not br.ok:
                    continue
                hits = sorted(set(re.findall(
                    r'["\'](/(?:api|graphql|v1|v2)/[A-Za-z0-9/_\-{}.]*)["\']',
                    br.text or "")))
                if hits:
                    api_hits.append({"bundle": burl, "paths": hits[:40]})
            findings["api_paths_in_bundles"] = api_hits
        else:
            findings["city_selector_error"] = home.status or home.error

    conn.commit()
    _write_discovery_doc(findings)
    finish_run(conn, run_id, True, {"slugs": len(findings.get("city_selector_slugs", []))})
    print(f"discovery written to {DOCS / 'discovery.md'}")
    finish(conn, args)


def _write_discovery_doc(f: dict) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "discovery.md"
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = "<!-- AUTOGENERATED: phase1 discover -->"
    head = prev.split(marker)[0].rstrip() if marker in prev else prev.rstrip()

    L = [head, "", marker, "", f"## Automated discovery — {now()}", ""]
    L.append(f"**robots.txt**: HTTP {f.get('robots', {}).get('status')}")
    L.append("")
    L.append("```")
    L.append((f.get("robots", {}).get("body") or "")[:1500] or "(empty)")
    L.append("```")
    L.append("")
    L.append("**Sitemaps probed**")
    for s in f.get("sitemaps", []):
        L.append(f"- `{s['url']}` -> HTTP {s['status']} ({s['bytes']} bytes)")
    if "sitemap_urls" in f:
        L.append("")
        L.append(f"A sitemap exists with **{f['sitemap_urls']} URLs** — that is the "
                 "complete URL list and supersedes the ID sweep as the primary "
                 "source. Sample:")
        for u in f.get("sitemap_sample", []):
            L.append(f"- {u}")
    else:
        L.append("")
        L.append("No usable sitemap — the ID sweep is the primary enumeration route.")
    L.append("")
    L.append("**City selector slugs** (the country mapping is built from these, "
             "never hardcoded):")
    L.append("")
    L.append(", ".join(f"`{s}`" for s in f.get("city_selector_slugs", [])) or "_none found_")
    if f.get("unmapped_slugs"):
        L.append("")
        L.append("> **Unmapped slugs — these are errors, not defaults.** Add them to "
                 "`lib/cities.py` before the sweep: "
                 + ", ".join(f"`{s}`" for s in f["unmapped_slugs"]))
    L.append("")
    L.append("**API paths found in JS bundles** (fallback 3 — a list/detail "
             "endpoint would beat the sweep outright):")
    L.append("")
    if f.get("api_paths_in_bundles"):
        for b in f["api_paths_in_bundles"]:
            L.append(f"- `{b['bundle']}`")
            for p in b["paths"]:
                L.append(f"  - `{p}`")
    else:
        L.append("_none — no JSON API surface exposed in the bundles; the sweep stands._")
    L.append("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------
def cmd_sweep(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.sweep")
    known = load_known_slugs(conn)

    done: set[int] = set()
    if args.resume:
        done = {r[0] for r in conn.execute("SELECT tableonline_id FROM enumeration_log")}
        print(f"resume: {len(done)} IDs already probed")

    ids = [i for i in range(args.start, args.end + 1) if i not in done]
    if args.limit:
        ids = ids[:args.limit]

    counts = {"probed": 0, "hits": 0, "stored": 0, "rejected": 0,
              "needs_review": 0, "unmapped_slug": 0, "errors": 0}

    with PoliteClient() as client:
        for tid in ids:
            url = to.probe_url(tid)
            r = client.get(url)
            counts["probed"] += 1

            if r.error:
                counts["errors"] += 1
                record_failure(conn, PHASE, url, r.error, tid)
                conn.commit()
                continue

            parsed = to.parse_listing(r.text, tid) if r.ok else {}
            upsert(conn, "enumeration_log", {"tableonline_id": tid}, {
                "http_status": r.status,
                "canonical": parsed.get("canonical"),
                "og_title": parsed.get("og_title_raw"),
                "checked_at": now(),
                "note": parsed.get("reason"),
            })

            if not r.ok:
                conn.commit()
                if counts["probed"] % 50 == 0:
                    print(f"  {tid}: {counts['hits']} hits / {counts['probed']} probed")
                continue

            counts["hits"] += 1
            # Every 200 is stored raw first; parsing is a separate step.
            store_raw(RAW_PAGES / f"{tid}.html", r.text)

            if not parsed.get("ok"):
                # Guard 1: a blank name is a deprecated/merged record, not a lead.
                counts["rejected"] += 1
                record_failure(conn, PHASE, url,
                               f"not ingested: {parsed.get('reason')}", tid)
                conn.commit()
                continue

            slug = parsed["city_slug"]
            country, source = country_for_slug(slug, known)
            if country is None:
                counts["unmapped_slug"] += 1
                record_failure(conn, PHASE, url,
                               f"unmapped city slug {slug!r} — country left NULL", tid)
                record_slug(conn, slug or "", None, None, "unmapped")

            needs_review = parsed["needs_review"] or (1 if country is None else 0)
            reason = parsed["review_reason"]
            if country is None:
                note = f"unmapped city slug {slug!r}"
                reason = f"{reason}; {note}" if reason else note

            upsert(conn, "restaurants", {"tableonline_id": parsed["tableonline_id"]}, {
                "name": parsed["name"],
                "restaurant_slug": parsed["restaurant_slug"],
                "city_slug": slug,
                "city": None,
                "country": country,
                "description": parsed["description"],
                "og_image": parsed["og_image"],
                "image_id": parsed["image_id"],
                "tableonline_url": to.canonical_url(slug, parsed["restaurant_slug"],
                                                    parsed["tableonline_id"])
                                   if slug and parsed["restaurant_slug"] else parsed["canonical"],
                "discovery_source": "id_enum",
                "needs_review": needs_review,
                "review_reason": reason,
                "phase1_at": now(),
                "phase2_status": "pending",
            })
            counts["stored"] += 1
            if needs_review:
                counts["needs_review"] += 1
            conn.commit()

            if counts["probed"] % 50 == 0:
                print(f"  {tid}: {counts['hits']} hits / {counts['probed']} probed")

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    _assign_tenure_buckets(conn)
    finish(conn, args)


def _assign_tenure_buckets(conn) -> None:
    """Quartile the tableonline_id. Bucket 1 = lowest IDs = longest-tenured
    customer, which is the strongest "you're renting your own customers"
    pitch. Bucket 4 = recent signup, still evaluating."""
    ids = [r[0] for r in conn.execute(
        "SELECT tableonline_id FROM restaurants ORDER BY tableonline_id")]
    if not ids:
        return
    n = len(ids)
    for idx, tid in enumerate(ids):
        bucket = min(4, int(idx * 4 / n) + 1)
        conn.execute("UPDATE restaurants SET tenure_bucket=? WHERE tableonline_id=?",
                     (bucket, tid))
    conn.commit()
    print(f"tenure buckets assigned across {n} restaurants")


# --------------------------------------------------------------------------
# crosscheck (Phase 1a-bis)
# --------------------------------------------------------------------------
def crosscheck_verdict(found: int, misses: list[int]) -> tuple[str, bool]:
    """The verdict, kept separate so it can be tested without a browser.

    An empty ground-truth set has no misses, so an earlier version reported
    "ZERO MISSES" when the city pages had failed to render — a check that
    cannot fail proves nothing. Too small a set is inconclusive, not a pass.
    """
    if found < MIN_GROUND_TRUTH:
        return (f"INCONCLUSIVE — only {found} restaurants were scraped from the "
                f"city pages (need at least {MIN_GROUND_TRUTH}). The listings "
                "did not render, so nothing was verified. Check "
                "raw/discovery/city-*.html.", False)
    if misses:
        return (f"{len(misses)} MISSES out of {found} — union the ID sweep with "
                "a Playwright crawl of all city pages on tableonline_id", False)
    return (f"ZERO MISSES out of {found} ground-truth restaurants — ID "
            "enumeration alone is safe", True)


def cmd_crosscheck(args) -> None:
    """Ground-truth the routing rule before building on it.

    The failure mode being ruled out is silent under-collection: an active
    restaurant that 404s on a wrong slug never appears and is never noticed.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.crosscheck")
    cities = args.cities.split(",") if args.cities else list(DEFAULT_CROSSCHECK_CITIES)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — run: pip install playwright && "
              "playwright install chromium", file=sys.stderr)
        finish_run(conn, run_id, False, {}, "playwright missing")
        return

    found: dict[int, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=_ua())
        page = ctx.new_page()
        for city in cities:
            url = f"{to.BASE}/en/{city}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                consent.dismiss(page)
                # Wait for the listings themselves, not for the network to go
                # quiet: this page polls, so networkidle never settles.
                try:
                    page.wait_for_function(
                        """() => document.querySelectorAll(
                             'a[href*="/en/"]').length > 20""", timeout=25000)
                except Exception:  # noqa: BLE001 — scroll anyway and see
                    pass
                _scroll_to_bottom(page)
                consent.hide(page)
                html = page.content()
            except Exception as exc:  # noqa: BLE001 — one bad city must not stop the check
                record_failure(conn, PHASE, url, str(exc))
                continue
            store_raw(RAW_PAGES.parent / "discovery" / f"city-{city}.html", html)
            for tid, path in to.extract_restaurant_links(html):
                found[tid] = path
                upsert(conn, "city_page_listings",
                       {"tableonline_id": tid, "city_slug": city},
                       {"url": to.BASE + path, "found_at": now()})
            print(f"  {city}: {len(to.extract_restaurant_links(html))} listings")
        browser.close()
    conn.commit()

    # Now probe every ground-truth ID through the slug-independent path.
    misses: list[int] = []
    with PoliteClient() as client:
        for tid in sorted(found):
            row = conn.execute(
                "SELECT http_status FROM enumeration_log WHERE tableonline_id=?",
                (tid,)).fetchone()
            status = row["http_status"] if row else None
            if status is None:
                r = client.get(to.probe_url(tid))
                status = r.status
                upsert(conn, "enumeration_log", {"tableonline_id": tid},
                       {"http_status": status, "checked_at": now(),
                        "note": "crosscheck"})
                conn.commit()
            if status != 200:
                misses.append(tid)

    verdict, ok = crosscheck_verdict(len(found), misses)
    counts = {"ground_truth_ids": len(found), "misses": len(misses),
              "miss_ids": misses[:50], "conclusive": len(found) >= MIN_GROUND_TRUTH}
    finish_run(conn, run_id, ok, counts, verdict)

    _append_crosscheck_doc(cities, found, misses, verdict)
    print(f"\n{verdict}")
    print(f"recorded in {DOCS / 'discovery.md'}")
    finish(conn, args)


def _scroll_to_bottom(page, rounds: int = 25) -> None:
    """City listings lazy-load; scroll until the height stops growing."""
    last = 0
    for _ in range(rounds):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)
        height = page.evaluate("document.body.scrollHeight")
        if height == last:
            break
        last = height
    # Some listings paginate behind a "show more" button instead.
    for _ in range(rounds):
        btn = page.query_selector(
            "button:has-text('more'), button:has-text('Näytä'), "
            "button:has-text('Lisää'), button:has-text('Rohkem')")
        if not btn:
            break
        try:
            btn.click(timeout=3000)
            page.wait_for_timeout(900)
        except Exception:  # noqa: BLE001 — button vanished mid-click, we are done
            break


def _ua() -> str:
    from lib.http import USER_AGENT
    return USER_AGENT


def _append_crosscheck_doc(cities, found, misses, verdict) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "discovery.md"
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    block = [
        "",
        "<!-- AUTOGENERATED: phase1 crosscheck -->",
        "",
        f"## Slug-independence cross-check — {now()}",
        "",
        f"Cities rendered: {', '.join(cities)}",
        "",
        "| metric | value |",
        "|---|---|",
        f"| IDs on live city pages (ground truth) | {len(found)} |",
        f"| of those, 404 on `/en/x/x/{{id}}` | {len(misses)} |",
        f"| verdict | {verdict} |",
        "",
    ]
    if misses:
        block.append("Missed IDs (active on a city page, 404 on the slug-independent "
                     "path — these would have been silently under-collected):")
        block.append("")
        block.append(", ".join(str(m) for m in misses[:100]))
        block.append("")
    marker = "<!-- AUTOGENERATED: phase1 crosscheck -->"
    base = prev.split(marker)[0].rstrip() if marker in prev else prev.rstrip()
    path.write_text(base + "\n" + "\n".join(block), encoding="utf-8")


# --------------------------------------------------------------------------
# coverage (Phase 1c)
# --------------------------------------------------------------------------
def cmd_coverage(args) -> None:
    """Sample IDs in range that were not collected and confirm they genuinely
    404 rather than having been missed. Silent under-collection is the main
    failure mode, so this is not optional."""
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.coverage")
    max_id = conn.execute("SELECT MAX(tableonline_id) FROM restaurants").fetchone()[0]
    if not max_id:
        print("no restaurants collected yet — run sweep first")
        finish_run(conn, run_id, False, {}, "no data")
        return

    collected = {r[0] for r in conn.execute("SELECT tableonline_id FROM restaurants")}
    candidates = [i for i in range(1, max_id + 1) if i not in collected]
    sample = random.Random(20260907).sample(candidates, min(args.sample, len(candidates)))

    results = {"sampled": len(sample), "confirmed_404": 0, "unexpected_200": 0,
               "unexpected_ids": [], "other": 0}
    with PoliteClient() as client:
        for tid in sample:
            r = client.get(to.probe_url(tid))
            if r.status == 404:
                results["confirmed_404"] += 1
            elif r.ok:
                results["unexpected_200"] += 1
                results["unexpected_ids"].append(tid)
                record_failure(conn, PHASE, to.probe_url(tid),
                               "coverage gap: uncollected ID returns 200", tid)
            else:
                results["other"] += 1
            upsert(conn, "enumeration_log", {"tableonline_id": tid},
                   {"http_status": r.status, "checked_at": now(),
                    "note": "coverage_sample"})
            conn.commit()

    ok = results["unexpected_200"] == 0
    note = ("coverage clean: every sampled uncollected ID genuinely 404s"
            if ok else
            f"COVERAGE GAP: {results['unexpected_200']} uncollected IDs return 200 "
            f"({results['unexpected_ids'][:20]}) — re-run the sweep")
    finish_run(conn, run_id, ok, results, note)
    print(json.dumps(results, indent=2))
    print(note)
    finish(conn, args)


# --------------------------------------------------------------------------
# slugs / recountry — fixing the country mapping without re-sweeping
# --------------------------------------------------------------------------
def cmd_slugs(args) -> None:
    """Every city slug seen, with its assigned country and a sample URL.

    An unmapped slug leaves country NULL by design rather than defaulting to
    FI. This is how you see which ones still need adding to lib/cities.py.
    """
    conn = open_db(args)
    known = load_known_slugs(conn)
    rows = conn.execute(
        "SELECT city_slug, COUNT(*) n, MIN(tableonline_url) url,"
        " MIN(name) sample FROM restaurants GROUP BY city_slug"
        " ORDER BY n DESC").fetchall()

    mapped, unmapped = [], []
    for r in rows:
        country, source = country_for_slug(r["city_slug"], known)
        (unmapped if country is None else mapped).append((r, country, source))

    print(f"{len(mapped)} mapped slugs, {len(unmapped)} unmapped\n")
    print(f"{'slug':32s} {'n':>4s}  country  source")
    print("-" * 70)
    for r, country, source in mapped:
        print(f"{(r['city_slug'] or ''):32s} {r['n']:>4d}  {country:7s}  {source}")

    if unmapped:
        print("\nUNMAPPED — country left NULL, not defaulted:")
        print("-" * 70)
        for r, _c, _s in unmapped:
            print(f"{(r['city_slug'] or '?'):32s} {r['n']:>4d}  e.g. {r['sample']}")
            print(f"{'':32s}       {r['url']}")
        print("\nAdd these to EE_SLUGS or FI_SEED_SLUGS in lib/cities.py, then:")
        print("  python -m src.phase1_enumerate recountry")
    finish(conn, args)


def cmd_recountry(args) -> None:
    """Re-apply the city-slug country mapping to rows already collected.

    Needed after lib/cities.py gains a slug: the sweep costs 40 minutes and
    the mapping is pure local logic, so it is re-derived rather than re-fetched.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.recountry")
    known = load_known_slugs(conn)
    counts = {"checked": 0, "assigned": 0, "changed": 0, "still_unmapped": 0}

    for row in conn.execute(
            "SELECT tableonline_id, city_slug, country, needs_review,"
            " review_reason FROM restaurants").fetchall():
        counts["checked"] += 1
        country, _source = country_for_slug(row["city_slug"], known)
        if country is None:
            counts["still_unmapped"] += 1
            continue
        if row["country"] == country:
            continue

        # Drop only the unmapped-slug note; an image-ID mismatch is a separate
        # reason and must survive.
        reason = row["review_reason"] or ""
        kept = [part.strip() for part in reason.split(";")
                if part.strip() and "unmapped city slug" not in part]
        new_reason = "; ".join(kept) or None
        conn.execute(
            "UPDATE restaurants SET country=?, city=?, needs_review=?,"
            " review_reason=? WHERE tableonline_id=?",
            (country, city_label(row["city_slug"]), 1 if kept else 0,
             new_reason, row["tableonline_id"]))
        counts["assigned"] += 1
        if row["country"]:
            counts["changed"] += 1
    conn.commit()

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    if counts["still_unmapped"]:
        print(f"\n{counts['still_unmapped']} rows still have no country — run "
              "`slugs` to see which and add them to lib/cities.py")
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = subcommands(p)
    sub.add_parser("discover", help="robots/sitemap/city selector/API discovery")
    s = sub.add_parser("sweep", help="ID sweep against /en/x/x/{id}")
    s.add_argument("--start", type=int, default=1)
    s.add_argument("--end", type=int, default=DEFAULT_MAX_ID)
    c = sub.add_parser("crosscheck", help="Phase 1a-bis slug-independence check")
    c.add_argument("--cities",
                   default=",".join(DEFAULT_CROSSCHECK_CITIES),
                   help="comma-separated city slugs to use as ground truth")
    cv = sub.add_parser("coverage", help="Phase 1c coverage check")
    cv.add_argument("--sample", type=int, default=50)
    sub.add_parser("slugs", help="list city slugs and which are unmapped")
    sub.add_parser("recountry", help="re-apply the country mapping, no network")
    args = p.parse_args(argv)
    {"discover": cmd_discover, "sweep": cmd_sweep,
     "crosscheck": cmd_crosscheck, "coverage": cmd_coverage,
     "slugs": cmd_slugs, "recountry": cmd_recountry}[args.cmd](args)


if __name__ == "__main__":
    main()
