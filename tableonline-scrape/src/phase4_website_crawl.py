"""Phase 4 — website crawl: emails, names and business IDs.

The highest-value phase: it feeds outreach *and* turns the Phase 5 registry
join from fuzzy matching into a primary-key lookup.

Three tiers, cheapest first (principle 5):

  Tier 1  The CMS's own content API. WordPress /wp-json returns every page
          with full body text in one request — no crawling, no JS, no
          rate-limit risk. Squarespace answers ?format=json.
  Tier 2  Static crawl (httpx + selectolax), up to 15 pages, chosen by anchor
          text first and slug second.
  Tier 3  Playwright, for Wix/Webflow and dynamic footers.
  Last    Firecrawl, only for Cloudflare-blocked domains, only with a key.

Page priority is deliberate: the GDPR privacy notice is crawled before the
contact page, because it must name a data controller with a contact address
and on a small restaurant that is frequently the owner, by name, while the
contact page shows only info@.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import cms_detect
from lib.crawl import extract_links, harvest
from lib.db import (add_contact, finish_run, insert_ignore, now,
                    record_failure, start_run, upsert)
from lib.http import PoliteClient, store_raw
from lib.normalise import slugify
from lib.pagekind import PAGE_KINDS, classify, rank_links
from lib.paths import RAW_WEBSITES
from src._cli import base_parser, finish, open_db

PHASE = "phase4"
MAX_PAGES = 15

SOURCE_BY_KIND = {
    "privacy": "website_privacy",
    "events": "website_events",
    "contact": "website_contact",
    "careers": "website_careers",
    "about": "website_other",
    "home": "website_other",
    "other": "website_other",
}

# Cloudflare / bot-wall signatures worth routing to Firecrawl rather than
# retrying, which only wastes politeness budget.
BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "attention required",
                 "checking your browser", "enable javascript and cookies")


def _raw_path(rid: int, url: str) -> Path:
    name = slugify(urlsplit(url).path) or "index"
    return RAW_WEBSITES / str(rid) / f"{name[:80]}.html"


def _looks_blocked(status: int, text: str) -> bool:
    if status in (403, 503):
        return True
    low = (text or "")[:4000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def _js_only(html: str) -> bool:
    """A shell page whose content never arrives without JS."""
    from lib.crawl import page_text
    return len(page_text(html)) < 400


def _record_page(conn, rid: int, url: str, kind: str, status: int,
                 html: str) -> Path:
    path = _raw_path(rid, url)
    store_raw(path, html)
    insert_ignore(conn, "website_pages", {
        "restaurant_id": rid, "url": url, "page_kind": kind,
        "priority": PAGE_KINDS.get(kind, (9,))[0] if kind in PAGE_KINDS else 9,
        "http_status": status, "raw_path": str(path.relative_to(RAW_WEBSITES.parent.parent)),
        "fetched_at": now()})
    return path


def _ingest(conn, rid: int, url: str, kind: str, html: str, country: str | None,
            counts: dict) -> None:
    """Harvest one page into contacts, business_ids and socials."""
    result = harvest(html, url, country)
    source = SOURCE_BY_KIND.get(kind, "website_other")

    for e in result["emails"]:
        confidence = "high" if e["channel"] in ("mailto", "cfemail", "obfuscated") \
                     else "medium"
        if e["contact_name"]:
            confidence = "high"
        created = add_contact(
            conn, rid, email=e["email"], contact_name=e["contact_name"],
            contact_role=e["contact_role"], source=source, source_url=url,
            confidence=confidence)
        if created:
            counts["contacts"] += 1
            if e["contact_name"]:
                counts["named"] += 1

    for b in result["business_ids"]:
        insert_ignore(conn, "business_ids", {
            "restaurant_id": rid, "business_id": b["business_id"],
            "country": b["country"], "source_url": url,
            "confidence": b["confidence"],
            "method": f"website_{kind}", "found_at": now()})
        counts["business_ids"] += 1

    for s in result["socials"]:
        insert_ignore(conn, "socials", {
            "restaurant_id": rid, "platform": s["platform"], "handle": s["handle"],
            "url": s["url"], "source_url": url, "found_at": now()})


# --------------------------------------------------------------------------
# Tier 1 — the CMS's own API
# --------------------------------------------------------------------------
def tier1(conn, client, rid: int, base: str, cms: str, country: str | None,
          counts: dict) -> int:
    """Returns the number of pages ingested, 0 if the tier does not apply."""
    pages = 0
    if cms == cms_detect.WORDPRESS:
        for url_fn in (cms_detect.wp_pages_url, cms_detect.wp_posts_url):
            r = client.get(url_fn(base))
            if not r.ok:
                continue
            for item in cms_detect.parse_wp_pages(r.text):
                page_url = item["url"] or base
                kind, _ = classify(item["title"], page_url)
                html = item["html"] or ""
                if not html.strip():
                    continue
                _record_page(conn, rid, page_url, kind, 200, html)
                _ingest(conn, rid, page_url, kind, html, country, counts)
                pages += 1
        # Sometimes exposes real staff names; 401/403 here is normal.
        ru = client.get(cms_detect.wp_users_url(base))
        if ru.ok:
            for name in cms_detect.parse_wp_users(ru.text):
                if add_contact(conn, rid, email=None, contact_name=name,
                               contact_role=None, source="website_other",
                               source_url=cms_detect.wp_users_url(base),
                               confidence="low"):
                    counts["named"] += 1

    elif cms == cms_detect.SQUARESPACE:
        urls = [base]
        for sm in cms_detect.SITEMAP_CANDIDATES:
            rs = client.get(urljoin(base, sm))
            if rs.ok and "<loc" in (rs.text or ""):
                urls += cms_detect.parse_sitemap(rs.text, limit=60)
                break
        host = urlsplit(base).netloc
        ranked = rank_links([(u, "") for u in urls], host, limit=MAX_PAGES)
        for url, kind, _prio in ranked:
            r = client.get(cms_detect.squarespace_json_url(url))
            if not r.ok:
                continue
            items = cms_detect.parse_squarespace_json(r.text)
            html = "\n".join(i["html"] for i in items if i.get("html"))
            if not html.strip():
                continue
            _record_page(conn, rid, url, kind, r.status, html)
            _ingest(conn, rid, url, kind, html, country, counts)
            pages += 1
    return pages


# --------------------------------------------------------------------------
# Tier 2 — static crawl
# --------------------------------------------------------------------------
def tier2(conn, client, rid: int, base: str, home_html: str, country: str | None,
          counts: dict) -> int:
    host = urlsplit(base).netloc
    links = extract_links(home_html, base)

    # A sitemap fills in pages the homepage nav does not link (privacy notices
    # are routinely reachable only from a footer that renders late).
    for sm in cms_detect.SITEMAP_CANDIDATES:
        rs = client.get(urljoin(base, sm))
        if rs.ok and "<loc" in (rs.text or ""):
            links += [(u, "") for u in cms_detect.parse_sitemap(rs.text, limit=200)]
            break

    ranked = rank_links(links, host, limit=MAX_PAGES)
    pages = 0
    for url, kind, _prio in ranked:
        if url.rstrip("/") == base.rstrip("/"):
            continue  # homepage already ingested
        r = client.get(url)
        if not r.ok:
            continue
        _record_page(conn, rid, url, kind, r.status, r.text)
        _ingest(conn, rid, url, kind, r.text, country, counts)
        pages += 1
    return pages


# --------------------------------------------------------------------------
# Tier 3 — Playwright
# --------------------------------------------------------------------------
CONSENT_SELECTORS = ("#onetrust-accept-btn-handler", ".cc-allow",
                     "[id*='accept']", "[class*='accept']",
                     "button:has-text('Hyväksy')", "button:has-text('Nõustu')")


def tier3(conn, rid: int, base: str, country: str | None, counts: dict) -> int:
    """JS-only sites. EU consent overlays can hide footers in headless mode;
    we dismiss them where we can, but footer links are usually in the DOM
    regardless of overlay state, so a failed click is not fatal."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        record_failure(conn, PHASE, base, "playwright not installed", rid)
        return 0

    from lib.http import USER_AGENT
    pages = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=USER_AGENT, locale="en-GB")
        page = ctx.new_page()
        try:
            page.goto(base, wait_until="networkidle", timeout=45000)
            _dismiss_consent(page)
            html = page.content()
            _record_page(conn, rid, base, "home", 200, html)
            _ingest(conn, rid, base, "home", html, country, counts)
            pages += 1

            host = urlsplit(base).netloc
            ranked = rank_links(extract_links(html, base), host, limit=MAX_PAGES)
            for url, kind, _prio in ranked:
                if url.rstrip("/") == base.rstrip("/"):
                    continue
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    _dismiss_consent(page)
                    sub = page.content()
                except Exception as exc:  # noqa: BLE001 — skip the page, keep the site
                    record_failure(conn, PHASE, url, str(exc)[:300], rid)
                    continue
                _record_page(conn, rid, url, kind, 200, sub)
                _ingest(conn, rid, url, kind, sub, country, counts)
                pages += 1
        except Exception as exc:  # noqa: BLE001
            record_failure(conn, PHASE, base, str(exc)[:300], rid)
        finally:
            browser.close()
    return pages


def _dismiss_consent(page) -> None:
    for sel in CONSENT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:  # noqa: BLE001 — overlay handling is best-effort
            continue


# --------------------------------------------------------------------------
# Firecrawl — last resort
# --------------------------------------------------------------------------
def firecrawl(conn, client, rid: int, base: str, country: str | None,
              counts: dict) -> int:
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not key:
        record_failure(conn, PHASE, base,
                       "blocked and no FIRECRAWL_API_KEY set", rid)
        return 0
    r = client.request(
        "POST", "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"url": base, "formats": ["html"]})
    if not r.ok:
        record_failure(conn, PHASE, base, f"firecrawl HTTP {r.status}", rid)
        return 0
    try:
        html = (json.loads(r.text).get("data") or {}).get("html") or ""
    except json.JSONDecodeError:
        return 0
    if not html:
        return 0
    _record_page(conn, rid, base, "home", 200, html)
    _ingest(conn, rid, base, "home", html, country, counts)
    return 1


# --------------------------------------------------------------------------
def cmd_crawl(args) -> None:
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.crawl")

    sql = ("SELECT w.restaurant_id, w.base_url, w.domain, w.status, r.country"
           " FROM websites w JOIN restaurants r ON r.tableonline_id = w.restaurant_id"
           " WHERE w.base_url IS NOT NULL")
    if args.resume:
        sql += " AND (w.status IS NULL OR w.status IN ('pending','failed'))"
    sql += " ORDER BY w.restaurant_id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()

    counts = {"sites": 0, "tier1": 0, "tier2": 0, "tier3": 0, "firecrawl": 0,
              "pages": 0, "contacts": 0, "named": 0, "business_ids": 0,
              "blocked": 0, "dead": 0}

    with PoliteClient() as client:
        for row in rows:
            rid, base = row["restaurant_id"], row["base_url"]
            country = row["country"]
            counts["sites"] += 1

            r = client.get(base)
            if r.error or r.status == 0:
                upsert(conn, "websites", {"restaurant_id": rid},
                       {"status": "dead", "error": r.error, "crawled_at": now()})
                record_failure(conn, PHASE, base, r.error or "no response", rid)
                counts["dead"] += 1
                conn.commit()
                continue

            home_html = r.text
            base = f"{urlsplit(str(r.url)).scheme}://{urlsplit(str(r.url)).netloc}" \
                   if r.url else base
            cms = cms_detect.detect(home_html, r.headers)
            pages = 0
            tier = None

            if _looks_blocked(r.status, home_html):
                counts["blocked"] += 1
                pages = firecrawl(conn, client, rid, base, country, counts)
                tier = "firecrawl"
                if pages:
                    counts["firecrawl"] += 1
            else:
                _record_page(conn, rid, base, "home", r.status, home_html)
                _ingest(conn, rid, base, "home", home_html, country, counts)
                pages = 1

                if args.tier in ("auto", "1") and cms in cms_detect.TIER1_CAPABLE:
                    got = tier1(conn, client, rid, base, cms, country, counts)
                    if got:
                        pages += got
                        tier = "tier1_cms"
                        counts["tier1"] += 1

                if tier is None and args.tier in ("auto", "3") \
                        and (cms in cms_detect.NEEDS_RENDER or _js_only(home_html)):
                    got = tier3(conn, rid, base, country, counts)
                    if got:
                        pages += got
                        tier = "tier3_render"
                        counts["tier3"] += 1

                if tier is None and args.tier in ("auto", "2"):
                    pages += tier2(conn, client, rid, base, home_html, country, counts)
                    tier = "tier2_static"
                    counts["tier2"] += 1

            counts["pages"] += pages
            upsert(conn, "websites", {"restaurant_id": rid}, {
                "cms": cms, "tier_used": tier, "pages_fetched": pages,
                "status": "ok" if pages else "failed", "crawled_at": now()})
            conn.commit()
            print(f"  {rid} {row['domain']}: cms={cms} tier={tier} pages={pages}")

    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def cmd_reparse(args) -> None:
    """Re-harvest every stored page without a single request.

    Principle 6 in action: improving the extraction never costs another crawl.
    """
    conn = open_db(args)
    run_id = start_run(conn, f"{PHASE}.reparse")
    counts = {"pages": 0, "contacts": 0, "named": 0, "business_ids": 0}
    rows = conn.execute(
        "SELECT p.restaurant_id, p.url, p.page_kind, p.raw_path, r.country"
        " FROM website_pages p"
        " JOIN restaurants r ON r.tableonline_id = p.restaurant_id").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    root = RAW_WEBSITES.parent.parent
    for row in rows:
        path = root / row["raw_path"]
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        _ingest(conn, row["restaurant_id"], row["url"], row["page_kind"], html,
                row["country"], counts)
        counts["pages"] += 1
        conn.commit()
    finish_run(conn, run_id, True, counts)
    print(json.dumps(counts, indent=2))
    finish(conn, args)


def main(argv=None) -> None:
    p = base_parser(__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="crawl restaurant websites")
    c.add_argument("--tier", choices=["auto", "1", "2", "3"], default="auto",
                   help="force a tier instead of choosing per site")
    sub.add_parser("reparse", help="re-harvest the stored corpus, no network")
    args = p.parse_args(argv)
    {"crawl": cmd_crawl, "reparse": cmd_reparse}[args.cmd](args)


if __name__ == "__main__":
    main()
