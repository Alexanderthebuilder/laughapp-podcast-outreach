"""City slug -> country mapping.

Finnish and Estonian restaurants share one domain and one sequential ID space;
the city slug is the only country discriminator. The mapping is built from the
site's own city selector at run time — the table below is a seed and a
fallback, not the source of truth. An unmapped slug is logged as an error and
left with country NULL; it is never silently defaulted to FI.
"""
from __future__ import annotations

import sqlite3

from .db import now, upsert
from .normalise import strip_diacritics

# Estonian cities on tableonline.fi. Everything else on the domain is Finnish,
# but see resolve(): "everything else -> FI" only applies to slugs we have
# actually seen in the site's city selector.
EE_SLUGS = {
    "tallinn", "tartu", "parnu", "parnu-linn", "narva", "haapsalu",
    "kuressaare", "viljandi", "rakvere", "pohja-eesti", "harjumaa",
    "pohja-tallinn", "pirita", "kadriorg", "pohjala",
}

FI_SEED_SLUGS = {
    "helsinki", "espoo", "vantaa", "tampere", "turku", "oulu", "jyvaskyla",
    "lahti", "kuopio", "pori", "joensuu", "lappeenranta", "vaasa", "rovaniemi",
    "seinajoki", "kotka", "hameenlinna", "mikkeli", "kokkola", "porvoo",
    "hyvinkaa", "nurmijarvi", "jarvenpaa", "kirkkonummi", "kerava", "tuusula",
    "naantali", "salo", "raisio", "kaarina", "imatra", "kouvola", "savonlinna",
    "levi", "yllas", "ruka", "saariselka", "tahko", "vierumaki", "nuuksio",
    "sipoo", "siuntio", "inkoo", "hanko", "raasepori", "lohja", "vihti",
}


def canonical_slug(slug: str | None) -> str:
    return strip_diacritics((slug or "").strip().lower()).replace("_", "-")


def country_for_slug(slug: str | None, known_slugs: set[str] | None = None
                     ) -> tuple[str | None, str]:
    """Return (country, source).

    country is None when the slug is not one we recognise. Callers must record
    that as an error and flag the row needs_review — an unmapped slug in a
    country-segmented outreach list is a real defect, not a rounding error.
    """
    s = canonical_slug(slug)
    if not s:
        return None, "unmapped"
    if s in EE_SLUGS:
        return "EE", "ee_list"
    if known_slugs and s in known_slugs:
        return "FI", "city_selector"
    if s in FI_SEED_SLUGS:
        return "FI", "fallback_map"
    return None, "unmapped"


def load_known_slugs(conn: sqlite3.Connection) -> set[str]:
    """Slugs harvested from the site's city selector by phase1 --discover."""
    return {r[0] for r in conn.execute(
        "SELECT city_slug FROM city_slugs WHERE source='city_selector'")}


def record_slug(conn: sqlite3.Connection, slug: str, city: str | None,
                country: str | None, source: str) -> None:
    upsert(conn, "city_slugs", {"city_slug": canonical_slug(slug)},
           {"city": city, "country": country, "source": source,
            "first_seen": now()})


def city_label(slug: str | None) -> str:
    """Human-readable city name from a slug, for Pipedrive and exports."""
    s = (slug or "").replace("-", " ").strip()
    return " ".join(w.capitalize() for w in s.split())
