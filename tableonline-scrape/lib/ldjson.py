"""schema.org JSON-LD extraction.

Phase 2 checks the rendered output for <script type="application/ld+json">
before DOM-scraping: a schema.org Restaurant object carries address and
telephone cleanly and does not break when the site is restyled.
"""
from __future__ import annotations

import json
import re

_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)

RESTAURANT_TYPES = {"restaurant", "foodestablishment", "localbusiness", "bar",
                    "cafeorcoffeeshop", "barorpub", "nightclub", "hotel",
                    "place", "organization"}


def blocks(html: str) -> list:
    """Every parseable JSON-LD payload in the page. Unparseable blocks are
    skipped — half the web ships JSON-LD with a trailing comma."""
    out = []
    for m in _SCRIPT_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            # A common variant is several concatenated objects; try the first.
            try:
                out.append(json.loads(raw[:raw.rindex("}") + 1]))
            except (json.JSONDecodeError, ValueError):
                continue
    return out


def _walk(node):
    """Yield every dict in a JSON-LD tree, including @graph members."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    if isinstance(t, str):
        return {t.lower()}
    if isinstance(t, list):
        return {str(x).lower() for x in t}
    return set()


def find_restaurant(html: str) -> dict | None:
    """The most specific business node in the page, or None.

    Prefers a true Restaurant over a generic LocalBusiness or Organization,
    since a page often carries both plus a WebSite node.
    """
    best, best_rank = None, 99
    rank = {"restaurant": 0, "bar": 1, "barorpub": 1, "cafeorcoffeeshop": 1,
            "foodestablishment": 2, "hotel": 3, "localbusiness": 4,
            "place": 5, "organization": 6}
    for payload in blocks(html):
        for node in _walk(payload):
            if not isinstance(node, dict):
                continue
            types = _types(node) & RESTAURANT_TYPES
            if not types:
                continue
            r = min(rank.get(t, 9) for t in types)
            if r < best_rank:
                best, best_rank = node, r
    return best


def extract(html: str) -> dict:
    """Normalise a schema.org business node into our field names."""
    node = find_restaurant(html)
    if not node:
        return {}

    def s(v):
        if isinstance(v, str):
            return v.strip() or None
        if isinstance(v, list) and v:
            return s(v[0])
        return None

    addr = node.get("address")
    if isinstance(addr, list) and addr:
        addr = addr[0]
    street = postal = city = country = None
    if isinstance(addr, dict):
        street = s(addr.get("streetAddress"))
        postal = s(addr.get("postalCode"))
        city = s(addr.get("addressLocality"))
        country = s(addr.get("addressCountry"))
        if isinstance(addr.get("addressCountry"), dict):
            country = s(addr["addressCountry"].get("name"))
    elif isinstance(addr, str):
        street = addr.strip() or None

    agg = node.get("aggregateRating")
    if isinstance(agg, list) and agg:
        agg = agg[0]
    score = count = None
    if isinstance(agg, dict):
        try:
            score = float(str(agg.get("ratingValue")).replace(",", ".")) \
                if agg.get("ratingValue") is not None else None
        except (TypeError, ValueError):
            score = None
        try:
            raw = agg.get("reviewCount", agg.get("ratingCount"))
            count = int(re.sub(r"\D", "", str(raw))) if raw is not None else None
        except (TypeError, ValueError):
            count = None

    cuisine = node.get("servesCuisine")
    if isinstance(cuisine, str):
        cuisine = [c.strip() for c in re.split(r"[,/|]", cuisine) if c.strip()]
    elif not isinstance(cuisine, list):
        cuisine = []

    geo = node.get("geo") if isinstance(node.get("geo"), dict) else {}

    return {
        "name": s(node.get("name")),
        "street_address": street,
        "postal_code": postal,
        "city": city,
        "country_name": country,
        "phone": s(node.get("telephone")),
        "website": s(node.get("url")) or s(node.get("sameAs")),
        "review_score": score,
        "review_count": count,
        "cuisine_tags": [str(c) for c in cuisine],
        "price_range": s(node.get("priceRange")),
        "lat": geo.get("latitude"),
        "lng": geo.get("longitude"),
    }
