"""Which page on a restaurant site is worth fetching, and in what order.

Anchor text first, slug second. Slug matching alone misses every site whose
privacy page lives at /page-17 or /?p=45 — anchor text catches those.

Priority is deliberate (Phase 4b): the GDPR privacy notice outranks the
contact page, because EU law forces it to name a data controller with a
contact address, and on a small restaurant that is frequently the owner by
name, while the contact page shows only info@.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .normalise import strip_diacritics

# kind -> (priority, anchor phrases, slug fragments). Lower priority = fetch first.
PAGE_KINDS: dict[str, tuple[int, tuple[str, ...], tuple[str, ...]]] = {
    "privacy": (1, (
        # FI
        "tietosuojaseloste", "tietosuoja", "tietosuojakaytanto", "tietosuojakäytäntö",
        "rekisteriseloste", "evasteet", "evästeet", "kayttoehdot", "käyttöehdot",
        # EE
        "privaatsuspoliitika", "privaatsustingimused", "privaatsus", "andmekaitse",
        "isikuandmete tootlemine", "isikuandmete töötlemine", "kupsised", "küpsised",
        # EN
        "privacy policy", "privacy", "cookie policy", "cookies", "terms",
        "terms and conditions", "data protection",
    ), (
        "tietosuoja", "tietosuojaseloste", "rekisteriseloste", "evasteet",
        "kayttoehdot", "privaatsus", "privaatsuspoliitika", "andmekaitse",
        "kupsised", "privacy", "privacy-policy", "cookie-policy", "cookies",
        "terms", "gdpr",
    )),
    "events": (2, (
        "tilaisuudet", "yksityistilaisuudet", "ryhmavaraukset", "ryhmävaraukset",
        "juhlat", "kokoustilat", "yritystilaisuudet",
        "peod", "uritused", "üritused", "seminarid", "grupibroneeringud",
        "private events", "group bookings", "functions", "events", "private dining",
        "celebrations", "meetings",
    ), (
        "tilaisuudet", "yksityistilaisuudet", "ryhmavaraukset", "juhlat",
        "kokoustilat", "peod", "uritused", "private-events", "group-bookings",
        "functions", "events", "private-dining",
    )),
    "careers": (3, (
        "avoimet tyopaikat", "avoimet työpaikat", "tyopaikat", "työpaikat", "rekry",
        "toopakkumised", "tööpakkumised", "tookohad", "töökohad", "karjaar", "karjäär",
        "careers", "jobs", "work with us", "join us", "vacancies",
    ), (
        "tyopaikat", "rekry", "avoimet-tyopaikat", "tookohad", "toopakkumised",
        "careers", "jobs", "vacancies",
    )),
    "contact": (5, (
        "yhteystiedot", "ota yhteytta", "ota yhteyttä", "yhteys",
        "kontakt", "kontaktid", "vota uhendust", "võta ühendust",
        "contact", "contact us", "find us",
    ), (
        "yhteystiedot", "yhteys", "ota-yhteytta", "kontakt", "kontaktid",
        "contact", "contact-us",
    )),
    "about": (6, (
        "meista", "meistä", "tietoa meista", "tarina",
        "meist", "meie lugu",
        "about", "about us", "our story", "team", "our team", "henkilokunta",
        "henkilökunta", "meeskond",
    ), (
        "meista", "tarina", "meist", "about", "about-us", "our-story", "team",
        "henkilokunta", "meeskond",
    )),
}

PRIORITY_BY_KIND = {k: v[0] for k, v in PAGE_KINDS.items()}
HOME_PRIORITY = 4      # the footer of the homepage carries the business ID
OTHER_PRIORITY = 9

_SKIP_EXT = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3|ics|css|js)$", re.I)
_SKIP_PREFIX = ("mailto:", "tel:", "javascript:", "#", "data:")
_SKIP_PATH = re.compile(
    r"/(wp-admin|wp-login|feed|comments|tag|author|cart|checkout|my-account|"
    r"search|\?s=|wp-json)(/|$)", re.I)


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(s or "").lower()).strip()


def classify(anchor_text: str | None, url: str | None) -> tuple[str, int]:
    """Return (kind, priority) for a link. Anchor text wins over the slug."""
    text = _flat(anchor_text)
    path = _flat(urlsplit(url or "").path)

    if text:
        for kind, (prio, anchors, _slugs) in sorted(PAGE_KINDS.items(),
                                                    key=lambda kv: kv[1][0]):
            for phrase in anchors:
                p = _flat(phrase)
                # Word-boundary match so "contact" does not fire on "contactless"
                # and short EN words do not match inside Finnish compounds.
                if re.search(rf"(^|[^a-z0-9]){re.escape(p)}([^a-z0-9]|$)", text):
                    return kind, prio

    if path:
        for kind, (prio, _anchors, slugs) in sorted(PAGE_KINDS.items(),
                                                    key=lambda kv: kv[1][0]):
            for slug in slugs:
                if re.search(rf"(^|[/\-_]){re.escape(slug)}([/\-_.]|$)", path):
                    return kind, prio

    if path in ("", "/"):
        return "home", HOME_PRIORITY
    return "other", OTHER_PRIORITY


def is_crawlable(url: str, base_host: str) -> bool:
    """Same-host, non-asset, non-admin links only."""
    if not url or url.lower().startswith(_SKIP_PREFIX):
        return False
    parts = urlsplit(url)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return False
    host = (parts.netloc or base_host).lower().removeprefix("www.")
    if host != base_host.lower().removeprefix("www."):
        return False
    if _SKIP_EXT.search(parts.path or ""):
        return False
    if _SKIP_PATH.search(parts.path or ""):
        return False
    return True


def rank_links(links: list[tuple[str, str]], base_host: str, limit: int = 15
               ) -> list[tuple[str, str, int]]:
    """Rank (url, anchor_text) pairs into the fetch order, capped at `limit`.

    Returns (url, kind, priority). De-duplicates on URL, keeping the best
    classification seen for it — the same page is often linked from a nav item
    with a vague label and a footer link with the useful one.
    """
    best: dict[str, tuple[str, int]] = {}
    for url, anchor in links:
        if not is_crawlable(url, base_host):
            continue
        clean = url.split("#")[0].rstrip("/") or "/"
        kind, prio = classify(anchor, url)
        if clean not in best or prio < best[clean][1]:
            best[clean] = (kind, prio)
    ordered = sorted(best.items(), key=lambda kv: (kv[1][1], kv[0]))
    return [(u, k, p) for u, (k, p) in ordered[:limit]]
