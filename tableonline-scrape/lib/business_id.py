"""Business ID harvesting and validation — the Phase 5 registry join key.

Phase 4 lifts these off the restaurant's own website, which turns Phase 5 from
a fuzzy-matching problem into a primary-key lookup. That only holds if the IDs
are actually valid, so nothing leaves this module unvalidated:

  * Finland (Y-tunnus) — checksum verified. Without the checksum a bare
    \\d{7}-\\d regex collects phone numbers and dates.
  * Estonia (registrikood) — 8 bare digits is far too weak a pattern to trust
    on its own, so a keyword must appear within ~50 characters.
"""
from __future__ import annotations

import re
import unicodedata

# --- Finland ---------------------------------------------------------------

_YTUNNUS_RE = re.compile(r"\b(\d{7})-(\d)\b")
_YTUNNUS_WEIGHTS = (7, 9, 10, 5, 8, 4, 2)


def ytunnus_check_digit(body: str) -> int | None:
    """Check digit for the first seven digits, or None if the body is invalid.

    Weights [7,9,10,5,8,4,2], sum mod 11: remainder 0 -> 0, remainder 1 ->
    no valid check digit exists, otherwise 11 - remainder.
    """
    if len(body) != 7 or not body.isdigit():
        return None
    total = sum(w * int(d) for w, d in zip(_YTUNNUS_WEIGHTS, body))
    rem = total % 11
    if rem == 0:
        return 0
    if rem == 1:
        return None
    return 11 - rem


def validate_ytunnus(value: str | None) -> bool:
    """True when value is a checksum-valid Finnish business ID."""
    if not value:
        return False
    m = _YTUNNUS_RE.fullmatch(value.strip())
    if not m:
        return False
    expected = ytunnus_check_digit(m.group(1))
    return expected is not None and expected == int(m.group(2))


def normalise_ytunnus(value: str | None) -> str | None:
    """Return the canonical 1234567-8 form, zero-padding a stripped leading 0.

    PRH publishes some older IDs with a leading zero that sites drop.
    """
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) == 7:                      # leading zero was dropped
        digits = "0" + digits
    if len(digits) != 8:
        return None
    candidate = f"{digits[:7]}-{digits[7]}"
    return candidate if validate_ytunnus(candidate) else None


def find_ytunnus(text: str) -> list[str]:
    """All checksum-valid Y-tunnus in a block of text, de-duplicated in order."""
    seen: list[str] = []
    for m in _YTUNNUS_RE.finditer(text or ""):
        candidate = f"{m.group(1)}-{m.group(2)}"
        if validate_ytunnus(candidate) and candidate not in seen:
            seen.append(candidate)
    return seen


# --- Estonia ---------------------------------------------------------------

_REGKOOD_RE = re.compile(r"\b(\d{8})\b")
_KMKR_RE = re.compile(r"\b(EE\d{9})\b", re.IGNORECASE)

# A bare 8-digit run is a postcode range, a date, or a phone number as often as
# it is a registrikood, so one of these must sit within _KEYWORD_WINDOW chars.
_REGKOOD_KEYWORDS = (
    "registrikood", "reg. nr", "reg nr", "regnr", "reg-nr",
    "ariregistri kood", "ariregistrikood", "registry code",
    "registration number", "registration code", "company code",
)
_KEYWORD_WINDOW = 50

# Estonian registry codes for companies start 1x; 8-digit runs starting 0 or 9
# are almost always something else (dates, phone numbers).
_REGKOOD_PREFIXES = ("1", "8")


def _deaccent_lower(s: str) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


def validate_registrikood(value: str | None) -> bool:
    """Shape-only validation: 8 digits with a plausible leading digit.

    Estonia's registry code has no public checksum, which is exactly why the
    keyword corroboration in find_registrikood() is mandatory.
    """
    if not value:
        return False
    v = re.sub(r"\D", "", value)
    return len(v) == 8 and v[0] in _REGKOOD_PREFIXES


def find_registrikood(text: str) -> list[tuple[str, str]]:
    """Keyword-corroborated Estonian registry codes.

    Returns (code, matched_keyword) pairs. A candidate with no keyword inside
    the window is dropped rather than stored at low confidence — a wrong
    registry code produces a confidently wrong company match downstream.
    """
    if not text:
        return []
    flat = _deaccent_lower(text)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _REGKOOD_RE.finditer(flat):
        code = m.group(1)
        if not validate_registrikood(code) or code in seen:
            continue
        lo = max(0, m.start() - _KEYWORD_WINDOW)
        window = flat[lo:m.end() + _KEYWORD_WINDOW]
        for kw in _REGKOOD_KEYWORDS:
            if kw in window:
                out.append((code, kw))
                seen.add(code)
                break
    return out


def find_kmkr(text: str) -> list[str]:
    """Estonian VAT numbers (KMKR), EE + 9 digits."""
    seen: list[str] = []
    for m in _KMKR_RE.finditer(text or ""):
        v = m.group(1).upper()
        if v not in seen:
            seen.append(v)
    return seen


def extract_business_ids(text: str, country_hint: str | None = None) -> list[dict]:
    """Every defensible business ID in a page, with its confidence and method.

    country_hint orders the attempts but never suppresses the other country —
    an Estonian group can own a Helsinki venue and vice versa.
    """
    results: list[dict] = []
    for y in find_ytunnus(text):
        results.append({
            "business_id": y, "country": "FI",
            "confidence": "checksum_valid", "keyword": None,
        })
    for code, kw in find_registrikood(text):
        results.append({
            "business_id": code, "country": "EE",
            "confidence": "keyword_corroborated", "keyword": kw,
        })
    if country_hint:
        results.sort(key=lambda r: r["country"] != country_hint)
    return results
