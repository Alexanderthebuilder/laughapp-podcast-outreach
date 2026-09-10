"""Trade register extracts from Virre — Finnish officers, free of charge.

PRH made the electronic extract (sähköinen kaupparekisteriote) free in 2022
and it needs no login. It carries what the bulk open-data API does not: the
board, the managing director, and the company's own registered email address.
Keyed on the Y-tunnus, which Phase 5 already resolves.

Two things about the source shape the code:

  * Names are printed SURNAME FIRST — "Nuutinen Olli-Pekka" is Olli-Pekka
    Nuutinen. Greeting the wrong half is worse than not greeting at all, so
    the split is explicit rather than inherited from lib/givennames.py.
  * Auditors appear in the same table layout as directors. Ernst & Young is
    not a decision maker, so roles are allow-listed, never merely filtered.

Birth dates are printed beside every name and are deliberately discarded. A
date of birth adds nothing to outreach and turns a lead list into a far
heavier thing to hold, so it is dropped at parse time rather than stored and
excluded later.
"""
from __future__ import annotations

import re
from typing import Iterator, NamedTuple

# Roles worth contacting, best first. The rank decides which person becomes
# the greeting name when a company lists several.
ROLE_RANK = {
    "toimitusjohtaja": 0,            # managing director
    "puheenjohtaja": 1,              # chair of the board
    "varapuheenjohtaja": 2,
    "jasen": 3,                      # ordinary board member
    "varajasen": 4,                  # deputy — last resort
}
ROLE_LABEL = {
    "toimitusjohtaja": "managing director",
    "puheenjohtaja": "board chair",
    "varapuheenjohtaja": "deputy chair",
    "jasen": "board member",
    "varajasen": "deputy board member",
}
# Present in the same tables, never contacted: auditors are the company's
# supervisors, not its management, and are usually a firm rather than a person.
EXCLUDED_ROLES = {
    "tilintarkastaja", "paavastuullinen tilintarkastaja",
    "varatilintarkastaja",
}

_DATE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
# Every section repeats its own name with a registration stamp —
# "Toimitusjohtaja (Rekisteröity 09.01.2020 08:20:39)". That opens with a role
# and carries a date, so it looks exactly like a table row unless excluded.
_STAMP = re.compile(r"\(\s*rekister", re.IGNORECASE)
_BUSINESS_ID = re.compile(r"\b(\d{7})-(\d)\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A company appearing where a person should be: an auditing firm, carrying its
# own business ID. Never a contact.
_COMPANY_SUFFIX = re.compile(
    r"\b(oy|oyj|ab|ky|ry|tmi|as|ou|oü)\b|&|y-tunnus", re.IGNORECASE)


class Officer(NamedTuple):
    name: str            # "Olli-Pekka Nuutinen" — given name first
    surname: str
    given: str
    role: str            # a key of ROLE_RANK
    rank: int


def _fold(s: str) -> str:
    """Fold Finnish diacritics so role matching does not depend on encoding."""
    return (s.lower().replace("ä", "a").replace("ö", "o").replace("å", "a")
            .replace("é", "e").strip())


def _role_of(line: str) -> tuple[str, str] | None:
    """Split a table row into (role_key, remainder), longest role first.

    Rows read "<role> <Surname> <Given names> <dd.mm.yyyy>". Roles are matched
    longest-first so "Päävastuullinen tilintarkastaja" is not read as the
    excluded-but-shorter "tilintarkastaja" with a stray word in the name.
    """
    folded = _fold(line)
    known = sorted(set(ROLE_RANK) | EXCLUDED_ROLES, key=len, reverse=True)
    for role in known:
        if folded.startswith(role):
            return role, line[len(role):].strip()
    return None


def _split_name(raw: str) -> tuple[str, str] | None:
    """"Nuutinen Olli-Pekka" -> ("Nuutinen", "Olli-Pekka").

    Everything after the first token is the given name or names; the greeting
    uses the first of them. A single token is not a person — the register
    prints both parts for every natural person.
    """
    parts = [p for p in re.split(r"\s+", raw.strip(" ,.")) if p]
    if len(parts) < 2:
        return None
    return parts[0], " ".join(parts[1:])


def officers(text: str) -> list[Officer]:
    """Every contactable officer in an extract, best role first.

    One person may hold two roles — a managing director who also chairs the
    board is common in a small restaurant company — and is returned once, at
    the better of them.
    """
    best: dict[str, Officer] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or not _DATE.search(line) or _STAMP.search(line):
            continue
        split = _role_of(line)
        if not split:
            continue
        role, remainder = split
        if role in EXCLUDED_ROLES:
            continue
        # Drop the birth date rather than capture it.
        remainder = _DATE.sub("", remainder).strip()
        if _COMPANY_SUFFIX.search(remainder):
            continue
        name = _split_name(remainder)
        if not name:
            continue
        surname, given = name
        officer = Officer(f"{given} {surname}", surname, given,
                          role, ROLE_RANK[role])
        key = _fold(officer.name)
        if key not in best or officer.rank < best[key].rank:
            best[key] = officer
    return sorted(best.values(), key=lambda o: (o.rank, o.surname))


def registered_contact(text: str) -> dict:
    """The company's own contact block, as filed with the register.

    Worth more than it looks: it is a filed address rather than one scraped
    off a page, and it exists for companies whose website gave us nothing.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        folded = _fold(line)
        if folded.startswith("sahkoposti"):
            found = _EMAIL.search(line)
            if found:
                out["email"] = found.group(0).lower()
        elif folded.startswith("puhelin"):
            digits = line.split(None, 1)
            if len(digits) == 2 and any(c.isdigit() for c in digits[1]):
                out["phone"] = digits[1].strip()
        elif folded.startswith("www-osoite"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                out["website"] = parts[1].strip()
    return out


def business_id(text: str) -> str | None:
    found = _BUSINESS_ID.search(text)
    return f"{found.group(1)}-{found.group(2)}" if found else None


def parse_extract(text: str) -> dict:
    """Everything worth keeping from one extract."""
    return {"business_id": business_id(text),
            "officers": officers(text),
            **registered_contact(text)}
