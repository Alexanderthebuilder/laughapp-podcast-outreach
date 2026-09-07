"""Link extraction and per-page harvesting shared by the Phase 4 tiers."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from .business_id import extract_business_ids
from .emails import attribute_person, extract_emails, extract_socials
from .detail_parse import strip_tags

_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
                      re.IGNORECASE)


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(absolute_url, anchor_text) for every anchor in the page.

    selectolax is used when available (faster and more forgiving of broken
    markup); the regex path keeps the module importable without it.
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        out = []
        for m in _A_RE.finditer(html or ""):
            hm = _HREF_RE.search(m.group(1))
            if not hm:
                continue
            href = hm.group(1) or hm.group(2) or hm.group(3) or ""
            out.append((urljoin(base_url, href.strip()),
                        strip_tags(m.group(2))[:120]))
        return out

    tree = HTMLParser(html or "")
    out = []
    for node in tree.css("a"):
        href = (node.attributes or {}).get("href")
        if not href:
            continue
        text = (node.text(separator=" ", strip=True) or "")[:120]
        # An icon-only link often carries its label in aria-label or title.
        if not text:
            attrs = node.attributes or {}
            text = (attrs.get("aria-label") or attrs.get("title") or "")[:120]
        out.append((urljoin(base_url, href.strip()), text))
    return out


def page_text(html: str) -> str:
    return strip_tags(html)


def harvest(html: str, url: str, country_hint: str | None = None) -> dict:
    """Everything Phase 4d wants from a single page.

    Returns emails (each with an attributed name and role where one is
    defensible), business IDs, and social links.
    """
    text = page_text(html)
    emails = []
    for e in extract_emails(html, text):
        name, role = attribute_person(text, e["email"], e.get("offset"))
        emails.append({"email": e["email"], "channel": e["channel"],
                       "confidence": e["confidence"],
                       "contact_name": name, "contact_role": role})
    return {
        "emails": emails,
        "business_ids": extract_business_ids(text, country_hint),
        "socials": extract_socials(html),
        "text_len": len(text),
        "url": url,
    }


def same_host(url: str, host: str) -> bool:
    a = urlsplit(url).netloc.lower().removeprefix("www.")
    b = (host or "").lower().removeprefix("www.")
    return bool(a) and a == b
