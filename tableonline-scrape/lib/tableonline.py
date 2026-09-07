"""Parsing tableonline.fi pages.

The site is a client-rendered SPA, but the meta tags (og:title, og:description,
og:image, canonical) are server-injected and present on a plain HTTP GET. That
is the whole basis of Phase 1: no browser, no vendor, no separate URL-discovery
step.
"""
from __future__ import annotations

import html as htmllib
import re

BASE = "https://www.tableonline.fi"

# /en/{city-slug}/{restaurant-slug}/{numeric-id}, optionally /book
CANONICAL_RE = re.compile(
    r"/(?P<lang>[a-z]{2})/(?P<city>[^/]+)/(?P<slug>[^/]+)/(?P<id>\d+)(?:/book)?/?$")
# https://img.tableonline.fi/restaurant/309/...
IMAGE_ID_RE = re.compile(r"/restaurant/(\d+)/")

_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*TableOnline(?:\.fi)?\s*$", re.IGNORECASE)


def probe_url(tableonline_id: int) -> str:
    """The slug-independent probe path. The site routes on the trailing ID and
    ignores the slug for active listings, so this returns exactly the active
    restaurants and self-filters churned ones."""
    return f"{BASE}/en/x/x/{tableonline_id}"


def canonical_url(city_slug: str, restaurant_slug: str, tableonline_id: int) -> str:
    return f"{BASE}/en/{city_slug}/{restaurant_slug}/{tableonline_id}"


_TAG_RE = re.compile(r"<(meta|link)\b([^>]*)>", re.IGNORECASE)
# A quoted attribute value cannot contain its own quote character, so the value
# class is bounded. Using an unbounded (.*?) here lets a match run across tag
# boundaries and pick up a neighbouring tag's attribute.
_ATTR_RE = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")


def _tags(html: str, tag: str) -> list[dict]:
    """Every <meta>/<link> tag as a lowercased attribute dict."""
    out = []
    for m in _TAG_RE.finditer(html or ""):
        if m.group(1).lower() != tag:
            continue
        attrs = {}
        for a in _ATTR_RE.finditer(m.group(2)):
            value = a.group(2) or a.group(3) or a.group(4) or ""
            attrs[a.group(1).lower()] = htmllib.unescape(value).strip()
        out.append(attrs)
    return out


def _meta(html: str, prop: str) -> str | None:
    """Read a meta tag by property or name, attribute order independent."""
    want = prop.lower()
    for attrs in _tags(html, "meta"):
        if (attrs.get("property", "").lower() == want
                or attrs.get("name", "").lower() == want):
            content = attrs.get("content")
            if content:
                return content
    return None


def _canonical(html: str) -> str | None:
    for attrs in _tags(html, "link"):
        if "canonical" in attrs.get("rel", "").lower():
            href = attrs.get("href")
            if href:
                return href
    return None


def clean_title(raw: str | None) -> str | None:
    """Strip the " | TableOnline.fi" suffix.

    A record whose og:title is nothing but the suffix is a deprecated/merged
    listing; it returns None so the caller can refuse to ingest a blank name.
    """
    if not raw:
        return None
    name = _TITLE_SUFFIX_RE.sub("", htmllib.unescape(raw)).strip()
    return name or None


def parse_listing(html: str, probed_id: int | None = None) -> dict:
    """Extract everything a plain GET yields, with the Phase 1a parsing guards.

    Returns a dict always containing `ok`; when ok is False, `reason` explains
    why the record must not be ingested as a lead.
    """
    canonical = _canonical(html)
    og_title = _meta(html, "og:title")
    og_desc = _meta(html, "og:description")
    og_image = _meta(html, "og:image")

    out: dict = {
        "ok": False,
        "reason": None,
        "tableonline_id": probed_id,
        "name": clean_title(og_title),
        "description": og_desc or None,
        "og_image": og_image,
        "og_title_raw": og_title,
        "canonical": canonical,
        "city_slug": None,
        "restaurant_slug": None,
        "canonical_id": None,
        "image_id": None,
        "needs_review": 0,
        "review_reason": None,
    }

    if canonical:
        m = CANONICAL_RE.search(canonical)
        if m:
            out["city_slug"] = m.group("city")
            out["restaurant_slug"] = m.group("slug")
            out["canonical_id"] = int(m.group("id"))

    if og_image:
        m = IMAGE_ID_RE.search(og_image)
        if m:
            out["image_id"] = int(m.group(1))

    # Guard 1: reject an empty og:title (" | TableOnline.fi") outright.
    if not out["name"]:
        out["reason"] = "empty_og_title"
        return out

    tid = out["canonical_id"] or probed_id
    if tid is None:
        out["reason"] = "no_id"
        return out
    out["tableonline_id"] = tid

    # Guard 2: an image ID that disagrees with the URL ID signals a merged or
    # stale record. Keep it, but never treat it as a clean lead.
    if out["image_id"] is not None and out["image_id"] != tid:
        out["needs_review"] = 1
        out["review_reason"] = (
            f"image_id {out['image_id']} != tableonline_id {tid} "
            "(merged or stale record)")

    # The probed ID and the canonical ID disagreeing is the same class of problem.
    if probed_id is not None and out["canonical_id"] not in (None, probed_id):
        out["needs_review"] = 1
        prev = out["review_reason"]
        note = f"canonical_id {out['canonical_id']} != probed id {probed_id}"
        out["review_reason"] = f"{prev}; {note}" if prev else note

    out["ok"] = True
    return out


def extract_restaurant_links(html: str) -> list[tuple[int, str]]:
    """(id, path) for every restaurant link in a rendered city listing page."""
    seen: dict[int, str] = {}
    for m in re.finditer(r'href=["\'](/[a-z]{2}/[^"\'#?]+/\d+)(?:/book)?["\']',
                         html or "", re.IGNORECASE):
        path = m.group(1)
        cm = CANONICAL_RE.search(path)
        if cm:
            seen.setdefault(int(cm.group("id")), path)
    return sorted(seen.items())
