"""Field extraction from a rendered tableonline.fi detail page.

Order of preference, per Phase 2: JSON-LD first (a schema.org Restaurant node
carries address and telephone cleanly and survives a restyle), then structured
hints in the DOM (tel: links, map links, tag links), then text heuristics.

The DOM heuristics are deliberately structural rather than class-name based —
class names on an SPA change without notice, whereas a tel: href stays a tel:
href. Anything not found is left None so run_report shows the true fill rate
rather than a plausible guess.
"""
from __future__ import annotations

import html as htmllib
import re
from urllib.parse import parse_qs, unquote, urlsplit

from . import ldjson

# FI 5 digits; EE 5 digits. Both are \d{5}, so the country comes from the city
# slug, not from the postcode shape.
POSTAL_RE = re.compile(r"\b(\d{5})\b")
PHONE_RE = re.compile(r"(\+?(?:358|372)\s?[\d\s\-()]{5,15}\d|\b0\d{1,2}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b)")
TEL_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.IGNORECASE)
SCORE_RE = re.compile(r"\b([0-9](?:[.,][0-9])?)\s*/\s*(?:5|10)\b")
COUNT_RE = re.compile(r"(\d[\d\s]{0,6})\s*(?:reviews?|arvostelua|arviota|arvostelut|"
                      r"arvustust|arvustused|hinnangut)", re.IGNORECASE)

MICHELIN_RE = re.compile(r"\bmichelin\b", re.IGNORECASE)
OFFER_RE = re.compile(r"\b(special offer|offers?|tarjous|tarjoukset|etu|edut|"
                      r"pakkumine|pakkumised|soodustus)\b", re.IGNORECASE)
GIFTCARD_RE = re.compile(r"\b(gift ?card|lahjakortti|lahjakortit|kinkekaart|"
                         r"kinkekaardid)\b", re.IGNORECASE)

# Tag chips are rendered as search links: /en/search?tag=sushi, /en/tag/sushi
TAG_HREF_RE = re.compile(
    r'href=["\'][^"\']*(?:[?&](?:tag|tags|cuisine|category|kitchen|atmosphere)=|/tags?/)([^"\'&#]+)',
    re.IGNORECASE)
MAP_HREF_RE = re.compile(
    r'href=["\']https?://(?:www\.)?(?:google\.[a-z.]+/maps[^"\']*|maps\.google\.[a-z.]+[^"\']*)["\']',
    re.IGNORECASE)

CUISINE_HINTS = {
    "sushi", "japanese", "italian", "french", "nordic", "finnish", "estonian",
    "asian", "thai", "indian", "chinese", "vietnamese", "korean", "mexican",
    "spanish", "greek", "vegan", "vegetarian", "seafood", "steak", "burger",
    "pizza", "tapas", "fusion", "grill", "bbq", "scandinavian", "russian",
    "georgian", "mediterranean", "american", "bistro", "brasserie",
}
ATMOSPHERE_HINTS = {
    "romantic", "casual", "fine dining", "family", "business", "cosy", "cozy",
    "rooftop", "terrace", "bar", "brunch", "lunch", "dinner", "group",
    "celebration", "date", "live music", "waterfront", "view", "historic",
}


def strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def _clean_phone(raw: str) -> str:
    p = re.sub(r"[^\d+]", "", unquote(raw or ""))
    return p if len(re.sub(r"\D", "", p)) >= 6 else ""


def _address_from_map_link(html: str) -> str | None:
    """Google Maps links usually carry the address in q=/query=/destination=."""
    for m in MAP_HREF_RE.finditer(html or ""):
        url = m.group(0).split('"')[1] if '"' in m.group(0) else m.group(0)
        qs = parse_qs(urlsplit(url).query)
        for key in ("q", "query", "destination", "daddr"):
            if key in qs and qs[key]:
                value = unquote(qs[key][0]).strip()
                # Skip bare coordinate links — they carry no street name.
                if value and not re.fullmatch(r"[-\d.,\s]+", value):
                    return value
        m2 = re.search(r"/maps/place/([^/@?]+)", url)
        if m2:
            value = unquote(m2.group(1)).replace("+", " ").strip()
            if value and not re.fullmatch(r"[-\d.,\s]+", value):
                return value
    return None


def _split_tags(values: list[str]) -> tuple[list[str], list[str]]:
    cuisine, atmosphere = [], []
    for v in values:
        clean = unquote(v).replace("-", " ").replace("+", " ").strip().lower()
        if not clean or len(clean) > 40:
            continue
        if clean in CUISINE_HINTS:
            cuisine.append(clean)
        elif clean in ATMOSPHERE_HINTS:
            atmosphere.append(clean)
        else:
            # Unknown tag: cuisine is the more useful default for outreach
            # segmentation, and the raw value is preserved either way.
            cuisine.append(clean)
    return sorted(set(cuisine)), sorted(set(atmosphere))


def parse_detail(html: str) -> dict:
    """Everything Phase 2 wants from one rendered page."""
    text = strip_tags(html)
    out: dict = {
        "street_address": None, "postal_code": None, "city": None,
        "phone": None, "review_score": None, "review_count": None,
        "cuisine_tags": [], "atmosphere_tags": [],
        "is_michelin": 0, "has_active_offer": 0, "accepts_giftcard": 0,
        "source": [],
    }

    ld = ldjson.extract(html)
    if ld:
        out["source"].append("ld+json")
        out["street_address"] = ld.get("street_address")
        out["postal_code"] = ld.get("postal_code")
        out["city"] = ld.get("city")
        out["phone"] = ld.get("phone")
        out["review_score"] = ld.get("review_score")
        out["review_count"] = ld.get("review_count")
        if ld.get("cuisine_tags"):
            out["cuisine_tags"] = [c.lower() for c in ld["cuisine_tags"]]

    # tel: href beats a phone-shaped run of digits in body text.
    if not out["phone"]:
        m = TEL_HREF_RE.search(html or "")
        if m:
            phone = _clean_phone(m.group(1))
            if phone:
                out["phone"], _ = phone, out["source"].append("tel_href")
    if not out["phone"]:
        m = PHONE_RE.search(text)
        if m:
            phone = _clean_phone(m.group(1))
            if phone:
                out["phone"], _ = phone, out["source"].append("phone_regex")

    if not out["street_address"]:
        addr = _address_from_map_link(html)
        if addr:
            out["street_address"] = addr
            out["source"].append("map_link")

    if not out["postal_code"]:
        # Prefer a postcode inside the address line; fall back to page text,
        # since a map link carries the street but rarely the postcode.
        for haystack in (out["street_address"] or "", text[:3000]):
            m = POSTAL_RE.search(haystack)
            if m:
                out["postal_code"] = m.group(1)
                break

    if out["review_score"] is None:
        m = SCORE_RE.search(text)
        if m:
            try:
                out["review_score"] = float(m.group(1).replace(",", "."))
                out["source"].append("score_regex")
            except ValueError:
                pass
    if out["review_count"] is None:
        # Strip "4,3 / 5" first: the denominator sits directly in front of the
        # count and would otherwise be read as a thousands separator (5 128).
        m = COUNT_RE.search(SCORE_RE.sub(" ", text))
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                out["review_count"] = int(digits)

    if not out["cuisine_tags"] and not out["atmosphere_tags"]:
        tags = TAG_HREF_RE.findall(html or "")
        if tags:
            out["cuisine_tags"], out["atmosphere_tags"] = _split_tags(tags)
            out["source"].append("tag_links")
    elif out["cuisine_tags"]:
        out["cuisine_tags"], extra = _split_tags(out["cuisine_tags"])
        out["atmosphere_tags"] = sorted(set(out["atmosphere_tags"]) | set(extra))

    out["is_michelin"] = 1 if MICHELIN_RE.search(text) else 0
    out["has_active_offer"] = 1 if OFFER_RE.search(text) else 0
    out["accepts_giftcard"] = 1 if GIFTCARD_RE.search(text) else 0
    return out
