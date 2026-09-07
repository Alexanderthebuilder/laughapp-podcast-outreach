"""CMS detection and the Tier 1 shortcut (Phase 4a).

Most small restaurant sites run a known CMS with a content API. One authorised
request to /wp-json returns every page with full body text — no crawling, no
JS, no rate-limit risk. Detect before crawling anything.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

WORDPRESS = "wordpress"
SQUARESPACE = "squarespace"
WIX = "wix"
WEBFLOW = "webflow"
SHOPIFY = "shopify"
UNKNOWN = "unknown"

_SIGNS = [
    (WORDPRESS, (re.compile(r"/wp-content/", re.I),
                 re.compile(r"/wp-includes/", re.I),
                 re.compile(r'rel=["\']https://api\.w\.org/', re.I),
                 re.compile(r"wp-json", re.I))),
    (SQUARESPACE, (re.compile(r"squarespace\.com|static1\.squarespace", re.I),
                   re.compile(r"Squarespace\.afterBodyLoad", re.I),
                   re.compile(r'name=["\']generator["\'][^>]*Squarespace', re.I))),
    (WIX, (re.compile(r"wixstatic\.com|wixsite\.com|parastorage\.com", re.I),
           re.compile(r'name=["\']generator["\'][^>]*Wix', re.I))),
    (WEBFLOW, (re.compile(r"webflow\.com|assets\.website-files\.com", re.I),
               re.compile(r'name=["\']generator["\'][^>]*Webflow', re.I))),
    (SHOPIFY, (re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I),)),
]


def detect(html: str, headers: dict | None = None) -> str:
    """Best-guess CMS from homepage HTML plus response headers."""
    blob = html or ""
    link = (headers or {}).get("link", "") or (headers or {}).get("Link", "")
    if "api.w.org" in link:
        return WORDPRESS
    for name, patterns in _SIGNS:
        if any(p.search(blob) for p in patterns):
            return name
    return UNKNOWN


# Tiers: which CMSes have a content API worth taking, and which must be rendered.
TIER1_CAPABLE = {WORDPRESS, SQUARESPACE}
NEEDS_RENDER = {WIX, WEBFLOW}


def wp_pages_url(base: str, page: int = 1, per_page: int = 100) -> str:
    return urljoin(base, f"/wp-json/wp/v2/pages?per_page={per_page}&page={page}"
                         "&_fields=id,link,title,content")


def wp_posts_url(base: str, page: int = 1, per_page: int = 50) -> str:
    return urljoin(base, f"/wp-json/wp/v2/posts?per_page={per_page}&page={page}"
                         "&_fields=id,link,title,content")


def wp_users_url(base: str) -> str:
    """Sometimes exposes real staff names. Often disabled — treat 401/403 as
    normal, not as an error."""
    return urljoin(base, "/wp-json/wp/v2/users?per_page=100&_fields=id,name,slug")


def squarespace_json_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}format=json"


def parse_wp_pages(payload: str) -> list[dict]:
    """Flatten a wp/v2 pages or posts response into {url, title, html}.

    Returns [] on anything that is not a JSON array of objects — plenty of
    sites return an HTML 404 page from /wp-json with a 200 status.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        html = content.get("rendered", "") if isinstance(content, dict) else (content or "")
        title = item.get("title")
        title_s = title.get("rendered", "") if isinstance(title, dict) else (title or "")
        out.append({"url": item.get("link") or "", "title": title_s, "html": html})
    return out


def parse_wp_users(payload: str) -> list[str]:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    names = []
    for u in data:
        if isinstance(u, dict) and isinstance(u.get("name"), str):
            n = u["name"].strip()
            if n and len(n.split()) >= 2:
                names.append(n)
    return names


def parse_squarespace_json(payload: str) -> list[dict]:
    """Squarespace ?format=json — pull the body HTML out of collection items."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for item in (data.get("items") or []):
        if isinstance(item, dict):
            out.append({"url": item.get("fullUrl", ""),
                        "title": item.get("title", ""),
                        "html": item.get("body") or item.get("excerpt") or ""})
    main = data.get("mainContent")
    if isinstance(main, str) and main:
        out.append({"url": data.get("item", {}).get("fullUrl", "") if isinstance(data.get("item"), dict) else "",
                    "title": (data.get("website") or {}).get("siteTitle", "")
                             if isinstance(data.get("website"), dict) else "",
                    "html": main})
    return out


SITEMAP_CANDIDATES = ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml",
                      "/sitemap-index.xml", "/page-sitemap.xml")


def parse_sitemap(xml: str, limit: int = 500) -> list[str]:
    """URLs from a sitemap or sitemap index. Order preserved, de-duplicated."""
    urls, seen = [], set()
    for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.I):
        u = m.group(1).strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)
        if len(urls) >= limit:
            break
    return urls
