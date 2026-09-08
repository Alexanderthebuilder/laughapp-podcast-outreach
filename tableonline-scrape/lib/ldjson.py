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


# Fields that make a node the real subject of the page rather than a passing
# mention of another restaurant in a carousel.
_SUBSTANCE_KEYS = ("address", "telephone", "aggregateRating", "geo",
                   "servesCuisine", "priceRange", "openingHours",
                   "openingHoursSpecification", "description", "menu")

_TYPE_RANK = {"restaurant": 0, "bar": 1, "barorpub": 1, "cafeorcoffeeshop": 1,
              "foodestablishment": 2, "hotel": 3, "localbusiness": 4,
              "place": 5, "organization": 6}


def _substance(node: dict) -> int:
    return sum(1 for k in _SUBSTANCE_KEYS if node.get(k))


def find_restaurant(html: str, page_id: str | int | None = None) -> dict | None:
    """The node this page is actually about, or None.

    A detail page carries a dozen JSON-LD blocks — breadcrumbs, and ItemLists
    of other restaurants, each a Restaurant node of its own. Taking the first
    node of the best type picks whichever appears earliest, which is routinely
    a stub or a neighbouring venue.

    Selection, in order: a node whose @id matches this page, then the most
    specific type, then the node carrying the most substance.
    """
    best, best_key = None, None
    wanted = str(page_id) if page_id is not None else None

    for payload in blocks(html):
        for node in _walk(payload):
            if not isinstance(node, dict):
                continue
            types = _types(node) & RESTAURANT_TYPES
            if not types:
                continue
            rank = min(_TYPE_RANK.get(t, 9) for t in types)
            node_id = str(node.get("@id") or "")
            # An @id ending in /{id} identifies the page's own subject.
            id_match = bool(wanted) and (
                node_id.rstrip("/").endswith("/" + wanted) or node_id == wanted)
            # Sort key: id match first, then type, then substance (negated so
            # that a smaller tuple is a better node).
            key = (0 if id_match else 1, rank, -_substance(node))
            if best_key is None or key < best_key:
                best, best_key = node, key
    return best


def extract(html: str, page_id: str | int | None = None) -> dict:
    """Normalise a schema.org business node into our field names."""
    node = find_restaurant(html, page_id)
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
