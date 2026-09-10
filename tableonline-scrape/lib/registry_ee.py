"""Estonian e-Business Register open data.

Free bulk files at avaandmed.ariregister.rik.ee. Two are needed:

  * company details (`ettevotja_rekvisiidid`) — registrikood, name, legal form,
    status, address with EHAK codes;
  * representation / board members — the names of `juhatuse liikmed`. Personal
    ID codes are masked in open data; **the names are not**, which is the
    named-contact unlock for Tallinn and Tartu.

The published filenames and column sets change between releases, so nothing
here hardcodes either: columns are resolved by matching header names, and the
loader accepts CSV, JSON or either inside a zip. Anything unmatched is
reported rather than guessed at.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Iterator

from .normalise import normalise_name, normalise_postcode, strip_diacritics

OPEN_DATA_PAGE = "https://avaandmed.ariregister.rik.ee/en/downloading-open-data"

# header fragment -> our column. Matched against a de-accented, lowercased
# header, longest fragment first, so "ettevotja_nimi" beats "nimi".
COMPANY_COLUMNS: dict[str, str] = {
    "ariregistri_kood": "registrikood",
    "registrikood": "registrikood",
    "reg_kood": "registrikood",
    "ettevotja_nimi": "name",
    "arinimi": "name",
    "nimi": "name",
    "name": "name",
    "ettevotja_oiguslik_vorm": "legal_form",
    "oiguslik_vorm": "legal_form",
    "legal_form": "legal_form",
    "ettevotja_staatus_tekstina": "status",
    "ettevotja_staatus": "status",
    "staatus": "status",
    "status": "status",
    "asukoht_ettevotja_aadressis": "address",
    "aadress": "address",
    "address": "address",
    "tanav_maja_korter": "street",
    "tanav": "street",
    "indeks": "post_code",
    "sihtnumber": "post_code",
    "postiindeks": "post_code",
    "asukoha_ehak_tekstina": "city",
    "ehak_tekstina": "city",
    "linn": "city",
    "asukoha_ehak_kood": "ehak_code",
    "ehak": "ehak_code",
    "kmkr_nr": "vat_id",
    "kmkr": "vat_id",
    "emtak": "emtaks",
}

BOARD_COLUMNS: dict[str, str] = {
    "ariregistri_kood": "registrikood",
    "registrikood": "registrikood",
    "isiku_nimi": "person_name",
    "eesnimi_perenimi": "person_name",
    "nimi": "person_name",
    "name": "person_name",
    "eesnimi": "first_name",
    "nimi_perenimi": "person_name",
    "perenimi": "last_name",
    "isiku_tyyp": "person_type",
    "kaardi_tyyp": "role",
    "isiku_roll": "role",
    "roll": "role",
    "role": "role",
    "ametikoht": "role",
}

# Roles that are a named human worth contacting. A "osanik" (shareholder) may
# be a holding company rather than a person, so roles are recorded verbatim
# and filtered at use.
BOARD_ROLE_HINTS = ("juhatuse liige", "juhatuse esimees", "juhataja",
                    "board member", "prokurist", "likvideerija")


def _norm_header(h: str) -> str:
    h = strip_diacritics(h or "").lower().strip().strip('"﻿')
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def map_headers(headers: list[str], mapping: dict[str, str]) -> dict[int, str]:
    """Column index -> our field name, using the longest matching fragment."""
    out: dict[int, str] = {}
    fragments = sorted(mapping, key=len, reverse=True)
    for idx, raw in enumerate(headers or []):
        h = _norm_header(raw)
        if not h:
            continue
        if h in mapping:
            out[idx] = mapping[h]
            continue
        for frag in fragments:
            if frag in h:
                out[idx] = mapping[frag]
                break
    return out


def _open_text(path: Path) -> Iterator[tuple[str, io.TextIOBase]]:
    """Yield (member_name, text stream) for a csv/json file or a zip of them."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith((".csv", ".json")):
                    continue
                with zf.open(info) as fh:
                    data = fh.read()
                yield info.filename, io.StringIO(_decode(data))
    else:
        yield path.name, io.StringIO(_decode(path.read_bytes()))


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1257", "iso-8859-13", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _sniff(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        # Estonian public CSVs are semicolon-delimited far more often than not.
        return ";" if sample.count(";") >= sample.count(",") else ","


def iter_rows(path, mapping: dict[str, str]) -> Iterator[dict]:
    """Rows from a CSV/JSON/zip file, keyed by our field names.

    Unmapped columns are dropped; a file whose headers map to nothing yields
    nothing, which the caller reports rather than silently accepting.
    """
    for _member, stream in _open_text(Path(path)):
        head = stream.read(8192)
        stream.seek(0)
        stripped = head.lstrip()
        if stripped.startswith(("[", "{")):
            try:
                payload = json.load(stream)
            except json.JSONDecodeError:
                continue
            records = payload if isinstance(payload, list) else \
                next((v for v in (payload.get(k) for k in
                                  ("data", "items", "results", "ettevotjad"))
                      if isinstance(v, list)), [])
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                idx_map = map_headers(list(rec.keys()), mapping)
                keys = list(rec.keys())
                row = {idx_map[i]: rec[keys[i]] for i in idx_map
                       if rec.get(keys[i]) not in (None, "")}
                if row:
                    yield row
            continue

        reader = csv.reader(stream, delimiter=_sniff(head))
        try:
            headers = next(reader)
        except StopIteration:
            continue
        idx_map = map_headers(headers, mapping)
        if not idx_map:
            continue
        for cells in reader:
            row = {}
            for i, field in idx_map.items():
                if i < len(cells):
                    value = (cells[i] or "").strip()
                    if value:
                        row.setdefault(field, value)
            if row:
                yield row


def normalise_company(row: dict) -> dict | None:
    code = re.sub(r"\D", "", str(row.get("registrikood") or ""))
    if len(code) != 8:
        return None
    name = (row.get("name") or "").strip() or None
    address = (row.get("address") or "").strip() or None
    return {
        "registrikood": code,
        "name": name,
        "legal_form": (row.get("legal_form") or "").strip() or None,
        "status": (row.get("status") or "").strip() or None,
        "address": address,
        "street": (row.get("street") or "").strip() or None,
        "post_code": normalise_postcode(row.get("post_code")) or None,
        "city": (row.get("city") or "").strip() or None,
        "ehak_code": (row.get("ehak_code") or "").strip() or None,
        "vat_id": (row.get("vat_id") or "").strip() or None,
        "emtaks": (row.get("emtaks") or "").strip() or None,
        "name_norm": normalise_name(name),
        "raw_json": json.dumps(row, ensure_ascii=False),
    }


# Estonian and Finnish legal forms. A board member carrying one of these is a
# company, not someone to write to.
_LEGAL_FORM_TOKENS = {"ou", "oü", "as", "mtu", "mtü", "uu", "uü", "tu", "tü",
                      "fie", "sa", "kü", "ky", "oy", "ab", "ltd", "gmbh",
                      "oyj", "plc", "inc"}


def _is_legal_entity(name: str) -> bool:
    tokens = {strip_diacritics(t).lower().strip(".,") for t in name.split()}
    return bool(tokens & {strip_diacritics(t).lower() for t in _LEGAL_FORM_TOKENS})


def normalise_board_member(row: dict) -> dict | None:
    code = re.sub(r"\D", "", str(row.get("registrikood") or ""))
    if len(code) != 8:
        return None
    name = (row.get("person_name") or "").strip()
    if not name:
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        name = f"{first} {last}".strip()
    if not name or len(name.split()) < 2:
        return None      # a masked entry, or a single-word placeholder
    if _is_legal_entity(name):
        return None      # a company sitting on the board, not a person
    role = (row.get("role") or "").strip() or "juhatuse liige"
    return {"registrikood": code, "person_name": name, "role": role}


# --- nested person records -------------------------------------------------

# "Persons on registry card" ships as XML/JSON only, and each company record
# carries its persons in a nested list. The key names differ between releases,
# so both the list and the fields inside it are found by matching rather than
# by path.
_PERSON_LIST_HINTS = ("isik", "person", "kaardile", "kanne", "esindus")
_PERSON_NAME_HINTS = ("isiku_nimi", "nimi_arinimi", "person_name", "eesnimi",
                      "nimi", "name")
_PERSON_ROLE_HINTS = ("isiku_roll", "roll", "role", "kaardi_tyyp", "ametikoht")
_CODE_HINTS = ("ariregistri_kood", "registrikood", "reg_kood", "kood",
               "registry_code")


def _first_matching(record: dict, hints: tuple[str, ...]):
    """Value of the first key whose normalised name contains a hint."""
    for hint in hints:
        for key, value in record.items():
            if hint in _norm_header(key) and value not in (None, "", []):
                return value
    return None


def _person_lists(record: dict):
    """Nested lists that look like they hold people."""
    for key, value in record.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if any(h in _norm_header(key) for h in _PERSON_LIST_HINTS):
                yield value


def iter_person_rows(path) -> Iterator[dict]:
    """Flatten "Persons on registry card" into one row per person.

    Accepts the nested JSON the register publishes, and also a flat CSV of the
    same data, so a future release that adds a CSV needs no change here.
    """
    path = Path(path)
    for _member, stream in _open_text(path):
        head = stream.read(8192)
        stream.seek(0)
        if not head.lstrip().startswith(("[", "{")):
            # Flat file: the ordinary column mapping already handles it.
            for row in iter_rows(path, BOARD_COLUMNS):
                yield row
            return
        try:
            payload = json.load(stream)
        except json.JSONDecodeError:
            continue
        records = payload if isinstance(payload, list) else next(
            (v for v in payload.values() if isinstance(v, list)), [])
        for record in records:
            if not isinstance(record, dict):
                continue
            code = _first_matching(record, _CODE_HINTS)
            if not code:
                continue
            found_nested = False
            for people in _person_lists(record):
                for person in people:
                    if not isinstance(person, dict):
                        continue
                    name = _first_matching(person, _PERSON_NAME_HINTS)
                    if not name:
                        continue
                    found_nested = True
                    # An end date means the person has left the board.
                    ended = _first_matching(person, ("kehtivuse_lopp",
                                                     "end_date", "lopp_kpv",
                                                     "loppemise"))
                    if ended:
                        continue
                    yield {"registrikood": str(code),
                           "person_name": str(name),
                           "role": str(_first_matching(person, _PERSON_ROLE_HINTS)
                                       or "juhatuse liige")}
            if not found_nested:
                # Already one row per person.
                name = _first_matching(record, _PERSON_NAME_HINTS)
                if name:
                    yield {"registrikood": str(code), "person_name": str(name),
                           "role": str(_first_matching(record, _PERSON_ROLE_HINTS)
                                       or "juhatuse liige")}


def looks_like_xml(path) -> bool:
    path = Path(path)
    if path.suffix.lower() == ".xml":
        return True
    try:
        with open(path, "rb") as fh:
            return fh.read(200).lstrip().startswith(b"<?xml")
    except OSError:
        return False


def has_nested_people(path) -> bool:
    """True when the file is JSON whose records nest a list of people.

    This is the shape that identifies "Persons on registry card". Testing for
    a column mapping instead is ambiguous: the company file also carries a
    registry code and a name, so it matches the person mapping too.
    """
    path = Path(path)
    try:
        for _member, stream in _open_text(path):
            head = stream.read(4096)
            stream.seek(0)
            if not head.lstrip().startswith(("[", "{")):
                return False
            payload = json.load(stream)
            records = payload if isinstance(payload, list) else next(
                (v for v in payload.values() if isinstance(v, list)), [])
            for record in records[:50]:
                if isinstance(record, dict) and any(_person_lists(record)):
                    return True
            return False
    except (OSError, json.JSONDecodeError, StopIteration):
        return False
    return False


def discover(directory) -> dict:
    """Work out which downloaded file is which, by reading them.

    The register's filenames vary, and asking a caller to type them invites
    the shell to swallow angle brackets. Each candidate is classified by its
    structure: nested person lists mean the board file, a registry code plus a
    company name means the basic-data file.
    """
    directory = Path(directory)
    found: dict = {"companies": None, "board": None, "xml": [], "unknown": []}
    if not directory.is_dir():
        return found

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (
                ".csv", ".json", ".zip", ".xml"):
            continue
        if looks_like_xml(path):
            found["xml"].append(path)
            continue

        if found["board"] is None and has_nested_people(path):
            found["board"] = path
            continue

        try:
            companies = sum(1 for row, _ in zip(iter_rows(path, COMPANY_COLUMNS),
                                                range(5))
                            if normalise_company(row))
        except Exception:  # noqa: BLE001 — wrong shape
            companies = 0
        if companies >= 3 and found["companies"] is None:
            found["companies"] = path
            continue

        # A flat person list, which a future CSV release would produce.
        try:
            people = sum(1 for row, _ in zip(iter_rows(path, BOARD_COLUMNS),
                                             range(5))
                         if normalise_board_member(row))
        except Exception:  # noqa: BLE001
            people = 0
        if people >= 3 and found["board"] is None:
            found["board"] = path
            continue

        found["unknown"].append(path)
    return found


def is_active(status: str | None) -> bool:
    """Estonian status strings vary by release; anything naming deletion,
    liquidation or bankruptcy is treated as inactive."""
    s = strip_diacritics(status or "").lower()
    if not s:
        return True
    return not any(w in s for w in
                   ("kustutatud", "likvideerimisel", "pankrot", "loppenud",
                    "deleted", "liquidation", "bankrupt"))
