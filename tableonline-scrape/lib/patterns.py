"""Email pattern generation for a known name at a known domain (Phase 6).

Candidates, in the order Finnish and Estonian small businesses actually use:
  etunimi@            first@
  etunimi.sukunimi@   first.last@
  e.sukunimi@         f.last@
  etunimisukunimi@    firstlast@

Diacritics are folded ä->a, ö->o, å->a, õ->o, ü->u, š->s, ž->z, which is what
these mailboxes are actually provisioned as.
"""
from __future__ import annotations

import re

from .normalise import strip_diacritics

# Name particles that are not part of a mailbox local part.
_PARTICLES = {"von", "van", "de", "af", "la", "le", "der", "den", "di"}


def split_name(full_name: str) -> tuple[str, str] | None:
    """(first, last) folded to ASCII, or None when the name is unusable."""
    if not full_name:
        return None
    cleaned = re.sub(r"[^\w\s\-]", " ", strip_diacritics(full_name))
    parts = [p for p in cleaned.lower().split()
             if p and p not in _PARTICLES and len(p) > 1]
    if len(parts) < 2:
        return None
    # A middle name is dropped; the surname is the last token.
    return parts[0], parts[-1]


def candidates(full_name: str, domain: str) -> list[str]:
    """Ordered, de-duplicated candidate addresses. Most likely first."""
    split = split_name(full_name)
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not split or not domain or "." not in domain:
        return []
    first, last = split
    first = re.sub(r"[^a-z0-9]", "", first)
    last = re.sub(r"[^a-z0-9]", "", last)
    if not first or not last:
        return []
    locals_ = [first, f"{first}.{last}", f"{first[0]}.{last}", f"{first}{last}"]
    out, seen = [], set()
    for local in locals_:
        addr = f"{local}@{domain}"
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def infer_pattern(known: list[str], full_name: str) -> str | None:
    """Which pattern a domain already uses, inferred from addresses we have.

    A single confirmed address on the domain is worth more than four guesses:
    if the site publishes matti.virtanen@, then liisa.koskinen@ is the shape to
    try for a colleague, not a spread of four.
    """
    split = split_name(full_name)
    if not split:
        return None
    for addr in known:
        local = (addr or "").split("@")[0].lower()
        parts = [p for p in re.split(r"[._\-]", local) if p]
        if len(parts) == 2:
            if len(parts[0]) == 1:
                return "f.last"
            return "first.last"
        if len(parts) == 1 and len(parts[0]) > 3:
            return "first_or_firstlast"
    return None


def ordered_candidates(full_name: str, domain: str,
                       known_on_domain: list[str] | None = None) -> list[str]:
    """Candidates, re-ordered to put the domain's observed pattern first."""
    cands = candidates(full_name, domain)
    if not cands or not known_on_domain:
        return cands
    pattern = infer_pattern(known_on_domain, full_name)
    if not pattern:
        return cands
    split = split_name(full_name)
    if not split:
        return cands
    first, last = split
    preferred = {
        "first.last": f"{first}.{last}@{domain}",
        "f.last": f"{first[0]}.{last}@{domain}",
    }.get(pattern)
    if preferred and preferred in cands:
        cands.remove(preferred)
        cands.insert(0, preferred)
    return cands
