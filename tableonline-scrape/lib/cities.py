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
    # Cities and towns
    "tallinn", "tartu", "parnu", "narva", "haapsalu", "kuressaare",
    "viljandi", "rakvere", "otepaa", "johvi", "sillamae", "valga", "voru",
    "polva", "rapla", "paide", "keila", "maardu", "kohtla-jarve", "tapa",
    "elva", "kardla", "jogeva", "turi", "kunda", "loksa", "narva-joesuu",
    "toila", "kuremaa", "pold",
    # Islands, regions and areas
    "saaremaa", "hiiumaa", "muhu", "harjumaa", "laanemaa", "ida-virumaa",
    "laane-virumaa", "tartumaa", "parnumaa", "viljandimaa", "raplamaa",
    "jarvamaa", "jogevamaa", "polvamaa", "valgamaa", "vorumaa",
    "pohja-eesti", "louna-eesti", "laane-eesti",
    # Tallinn districts and nearby municipalities
    "pohja-tallinn", "pirita", "kadriorg", "vanalinn", "kesklinn", "kalamaja",
    "nomme", "haabersti", "lasnamae", "mustamae", "viimsi", "harku", "saue",
    "kiili", "rae", "joelahtme", "laulasmaa", "padise", "muuga", "peetri",
    "estonia", "eesti",
}

FI_SEED_SLUGS = {
    # Capital region
    "helsinki", "espoo", "vantaa", "kauniainen",
    # Largest cities
    "tampere", "turku", "oulu", "jyvaskyla", "lahti", "kuopio", "pori",
    "joensuu", "lappeenranta", "vaasa", "rovaniemi", "seinajoki", "kotka",
    "hameenlinna", "mikkeli", "kokkola", "porvoo", "hyvinkaa", "lohja",
    "jarvenpaa", "rauma", "kajaani", "kerava", "savonlinna", "nokia",
    "kouvola", "imatra", "riihimaki", "salo", "raisio", "kaarina", "naantali",
    "varkaus", "iisalmi", "raahe", "tornio", "kemi", "pietarsaari",
    "valkeakoski", "heinola", "forssa", "kuusamo", "kurikka", "kauhava",
    "ylojarvi", "kangasala", "pirkkala", "lempaala", "akaa", "sastamala",
    "ikaalinen", "virrat", "orivesi", "jamsa", "aanekoski", "laukaa",
    "muurame", "keuruu", "saarijarvi", "viitasaari", "siilinjarvi", "kuhmo",
    "sotkamo", "nurmes", "lieksa", "kitee", "outokumpu", "kontiolahti",
    "liperi", "ilomantsi", "pieksamaki", "hollola", "orimattila", "asikkala",
    "nastola", "hamina", "loviisa", "kirkkonummi", "nurmijarvi", "tuusula",
    "sipoo", "siuntio", "inkoo", "hanko", "raasepori", "vihti", "mantsala",
    "pornainen", "askola", "pukkila", "myrskyla", "lapinjarvi",
    "uusikaupunki", "laitila", "mynamaki", "masku", "nousiainen", "rusko",
    "lieto", "paimio", "sauvo", "kemionsaari", "parainen", "somero",
    "poytya", "aura", "loimaa", "huittinen", "kokemaki", "harjavalta",
    "nakkila", "ulvila", "eura", "sakyla", "eurajoki", "merikarvia",
    "kankaanpaa", "parkano", "kalajoki", "ylivieska", "nivala", "haapajarvi",
    "oulainen", "kempele", "liminka", "muhos", "ii", "pudasjarvi", "kemijarvi",
    "sodankyla", "inari", "ivalo", "utsjoki", "enontekio", "muonio", "kolari",
    "kittila", "pello", "ylitornio", "keminmaa", "ranua", "posio", "salla",
    "lapua", "alajarvi", "alavus", "kuortane", "ahtari", "ilmajoki",
    "kauhajoki", "teuva", "kristiinankaupunki", "narpio", "maalahti",
    "mustasaari", "laihia", "uusikaarlepyy", "kruunupyy", "kannus",
    "kaustinen", "mariehamn", "maarianhamina", "ahvenanmaa", "aland",
    # Ski resorts and holiday areas — common on a booking site
    "levi", "yllas", "akaslompolo", "ruka", "saariselka", "tahko",
    "vierumaki", "himos", "pyha", "luosto", "iso-syote", "syote", "koli",
    "vuokatti", "sappee", "messila", "ounasvaara", "olos", "pallas",
    "harriniva", "nuuksio", "serena", "flamingo",
    # Regions
    "lappi", "lapland", "uusimaa", "pirkanmaa", "varsinais-suomi",
    "pohjanmaa", "kainuu", "savo", "karjala", "hame", "satakunta",
    "kymenlaakso", "keski-suomi", "pohjois-karjala", "etela-savo",
    "pohjois-savo", "etela-karjala", "paijat-hame", "kanta-hame",
    "pohjois-pohjanmaa", "etela-pohjanmaa", "keski-pohjanmaa", "suomi",
    "finland",
    # Helsinki districts, in case the site segments by neighbourhood
    "kallio", "punavuori", "kamppi", "kruununhaka", "toolo", "etu-toolo",
    "taka-toolo", "eira", "ullanlinna", "katajanokka", "hakaniemi",
    "sornainen", "vallila", "arabianranta", "herttoniemi", "kulosaari",
    "lauttasaari", "munkkiniemi", "pasila", "ruoholahti", "jatkasaari",
    "kaartinkaupunki", "kaisaniemi", "kluuvi", "keskusta", "kamppi-punavuori",
    "helsinki-keskusta", "tapiola", "leppavaara", "matinkyla", "otaniemi",
    "espoonlahti", "myyrmaki", "tikkurila", "aviapolis",
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
