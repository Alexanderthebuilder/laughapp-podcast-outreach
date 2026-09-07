"""Name, address and diacritic normalisation shared across phases.

The registry join (Phase 5) and the Places accept test (Phase 3) both compare
strings that were typed by different people in different systems. Everything
that compares two names must go through normalise_name() so the comparison is
symmetric and reproducible.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Phase 6 pattern generation needs these exact folds for local-part guessing:
# ä->a, ö->o, å->a, õ->o, ü->u, š->s, ž->z
_FOLD = {
    "ä": "a", "ö": "o", "å": "a", "õ": "o", "ü": "u", "š": "s", "ž": "z",
    "æ": "ae", "ø": "o", "ß": "ss", "þ": "th", "ð": "d",
}

# Stripped before comparing a brand to a registered company name. "Aoi" has to
# reach "Ravintola Aoi Oy" — see Phase 5 join key 2.
_LEGAL_FORMS = {
    # Finnish / Swedish
    "oy", "ab", "oyj", "ky", "tmi", "ry", "osk", "oyab",
    # Estonian
    "ou", "as", "mtu", "fie", "tu",
}
# Descriptive words that are part of the trade name but not the brand.
_DESCRIPTIVE = {
    "ravintola", "ravintolat", "restaurant", "restaurants", "restoran",
    "resto", "kahvila", "baari", "bar", "bistro", "cafe", "kohvik",
    "toitlustus",
}
_COMPANY_TOKENS = _LEGAL_FORMS | _DESCRIPTIVE


def strip_diacritics(s: str) -> str:
    """Fold accented characters to ASCII using the Nordic/Baltic rules."""
    if not s:
        return ""
    out = []
    for ch in s:
        low = ch.lower()
        if low in _FOLD:
            folded = _FOLD[low]
            out.append(folded.upper() if ch.isupper() else folded)
        else:
            out.append(ch)
    decomposed = unicodedata.normalize("NFKD", "".join(out))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalise_text(s: str | None) -> str:
    """Lowercase, de-accent, collapse punctuation and whitespace."""
    if not s:
        return ""
    s = strip_diacritics(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalise_name(s: str | None) -> str:
    """normalise_text plus removal of legal forms and descriptive prefixes.

    When stripping would empty the string, legal forms alone are removed, so a
    company literally named "Kohvik OU" still compares equal to "Kohvik".
    """
    base = normalise_text(s)
    if not base:
        return ""
    tokens = [t for t in base.split() if t not in _COMPANY_TOKENS]
    if tokens:
        return " ".join(tokens)
    # An entirely generic name ("Kohvik OU"): keep the descriptive word so it
    # still compares equal to the same venue written without its legal form,
    # rather than falling all the way back to the raw string.
    tokens = [t for t in base.split() if t not in _LEGAL_FORMS]
    return " ".join(tokens) if tokens else base


def name_similarity(a: str | None, b: str | None) -> float:
    """0.0-1.0 similarity of two normalised names.

    Token-set containment is checked first: "aoi" vs "aoi helsinki" is a strong
    match that plain character ratio would score around 0.5.
    """
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return max(0.9, SequenceMatcher(None, na, nb).ratio())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(SequenceMatcher(None, na, nb).ratio(), jaccard)


_STREET_SUFFIX = {
    "katu": "katu", "gatan": "katu", "tie": "tie", "vagen": "tie",
    "tanav": "tanav", "tn": "tanav", "puiestee": "puiestee", "pst": "puiestee",
    "maantee": "maantee", "mnt": "maantee",
}

_ADDR_RE = re.compile(r"^(?P<street>.*?)[\s,]+(?P<number>\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?)\b")


def parse_street(address: str | None) -> tuple[str, str]:
    """Split a free-text street line into (street, building number).

    PRH stores these as separate fields (`street`, `buildingNumber`); the
    TableOnline listing and Google both give one line. Returns ("", "") when
    no number is present rather than guessing.
    """
    if not address:
        return "", ""
    line = address.split(",")[0].strip()
    m = _ADDR_RE.match(normalise_text(line))
    if not m:
        return normalise_street(line), ""
    return normalise_street(m.group("street")), m.group("number").replace(" ", "")


def normalise_street(street: str | None) -> str:
    """Normalise a street name, folding common abbreviations (mnt -> maantee)."""
    base = normalise_text(street)
    if not base:
        return ""
    return " ".join(_STREET_SUFFIX.get(t, t) for t in base.split())


def normalise_postcode(pc: str | None) -> str:
    """Digits only. PRH uses `postCode` (capital C) and zero-pads to 5."""
    if not pc:
        return ""
    digits = re.sub(r"\D", "", str(pc))
    return digits


def addresses_agree(a_street: str, a_number: str, a_post: str,
                    b_street: str, b_number: str, b_post: str) -> bool:
    """Phase 5 join key 3. Requires postcode and street to agree; building
    number must agree when both sides have one."""
    if not a_street or not b_street:
        return False
    if normalise_postcode(a_post) != normalise_postcode(b_post):
        return False
    if name_similarity(a_street, b_street) < 0.9:
        return False
    if a_number and b_number:
        return a_number.lower().replace(" ", "") == b_number.lower().replace(" ", "")
    return True


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", strip_diacritics(s or "").lower()).strip("-")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Phase 3 accepts inside 150 m."""
    from math import radians, sin, cos, asin, sqrt
    r = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(h))
