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
    """Yield (member_name, text stream) for a csv/json file or a zip of them.

    Streamed off disk rather than read into memory. The register's person
    file is over a gigabyte; reading it into bytes and decoding that to a str
    costs several gigabytes before any parsing starts, which is enough to
    take a modest VPS down.
    """
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith((".csv", ".json")):
                    continue
                with zf.open(info) as fh:
                    yield info.filename, _as_text(fh)
    else:
        with path.open("rb") as fh:
            yield path.name, _as_text(fh)


def _as_text(binary) -> io.TextIOBase:
    return io.TextIOWrapper(binary, encoding=_sniff_encoding(binary),
                            errors="replace", newline="")


def _sniff_encoding(binary) -> str:
    """Pick an encoding from the first 64 kB rather than the whole file.

    errors="replace" covers a byte further in that the prefix did not predict:
    one mangled character in a name is a far smaller price than holding the
    file in memory to be certain.
    """
    head = binary.read(65536)
    binary.seek(0)
    for enc in ("utf-8-sig", "utf-8", "cp1257", "iso-8859-13"):
        try:
            # Trim any multi-byte character straddling the cut, which would
            # otherwise reject a correct encoding.
            head[:-4].decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _json_records(stream) -> Iterator[dict]:
    """Stream the records out of a JSON file without building it in memory.

    Handles both shapes the register publishes: a bare top-level array, and an
    object wrapping one. The wrapping key is found by walking events rather
    than guessed from a list of names, so a renamed key still works.
    """
    import ijson

    # ijson reads bytes. Handing it the decoded text stream makes it convert
    # back on the fly, which on a gigabyte is real work for nothing — so
    # reach through to the byte stream the wrapper sits on.
    raw = getattr(stream, "buffer", None)
    if raw is not None:
        raw.seek(0)
        stream = raw
    else:
        # An in-memory stream, which only the tests supply; encoding it is
        # cheap there and never happens against a real file.
        stream.seek(0)
        stream = io.BytesIO(stream.read().encode("utf-8"))

    prefix = None
    for path_, event, _value in ijson.parse(stream):
        if event in ("start_map", "start_array") and path_:
            prefix = path_ if event == "start_map" else None
            if prefix:
                break
    if prefix is None:
        return
    stream.seek(0)
    for record in ijson.items(stream, prefix):
        if isinstance(record, dict):
            yield record


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
            for rec in _json_records(stream):
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
# Lists that sit beside the people and match those hints without holding any:
# esindusoiguse_* are representation rules, carrying card and entry numbers
# and no name at all. Walking them costs nothing but noise in the counts.
_NOT_PERSON_LISTS = ("esindusoigus", "normaalregulatsioon", "eritingimus",
                     "hooneyhistu")

# A natural person's name arrives in two fields — eesnimi and nimi_arinimi —
# and matching whichever comes first returns the surname on its own. Since a
# one-word name is then rejected as a placeholder, first-match quietly threw
# away every Estonian person in the file.
_GIVEN_NAME_HINTS = ("eesnimi", "first_name", "given_name")
_SURNAME_HINTS = ("nimi_arinimi", "perekonnanimi", "last_name", "surname")
# Only for releases that publish one combined field.
_PERSON_NAME_HINTS = ("isiku_nimi", "person_name", "nimi_arinimi", "nimi",
                      "name")
# The _tekstina variant is the role in words; the plain one is a code.
_PERSON_ROLE_HINTS = ("isiku_roll_tekstina", "roll_tekstina", "isiku_roll",
                      "roll", "role", "ametikoht")


def person_name(person: dict) -> str | None:
    """A person's full name, given name first.

    Both halves or nothing: a surname on its own cannot open an email, and
    letting one through means greeting somebody by their family name.
    """
    given = _first_matching(person, _GIVEN_NAME_HINTS)
    surname = _first_matching(person, _SURNAME_HINTS)
    if given and surname:
        return f"{str(given).strip()} {str(surname).strip()}".strip()
    # A legal entity on the board has no given name, and a combined-field
    # release has no separate one; both are handled downstream.
    single = _first_matching(person, _PERSON_NAME_HINTS)
    return str(single).strip() if single else None
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
        if not (isinstance(value, list) and value and isinstance(value[0], dict)):
            continue
        folded = _norm_header(key)
        if any(bad in folded for bad in _NOT_PERSON_LISTS):
            continue
        if any(h in folded for h in _PERSON_LIST_HINTS):
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
        for record in _json_records(stream):
            code = _first_matching(record, _CODE_HINTS)
            if not code:
                continue
            found_nested = False
            for people in _person_lists(record):
                for person in people:
                    if not isinstance(person, dict):
                        continue
                    name = person_name(person)
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
                name = person_name(record)
                if name:
                    yield {"registrikood": str(code), "person_name": str(name),
                           "role": str(_first_matching(record, _PERSON_ROLE_HINTS)
                                       or "juhatuse liige")}


def inspect_person_file(path, limit: int = 20000) -> dict:
    """Count why person rows are and are not produced, over a sample.

    Written after a 1 GB file yielded board members for 6% of companies. The
    loader drops a record at four separate points and reports none of them,
    so the difference between "the key names changed" and "everyone looks
    resigned" was invisible from the outside.
    """
    seen: dict = {"records": 0, "no_code": 0, "no_person_list": 0,
                  "empty_person_list": 0, "people": 0, "no_name": 0,
                  "ended": 0, "yielded": 0,
                  "record_keys": {}, "list_keys": {}, "person_keys": {}}
    for _member, stream in _open_text(Path(path)):
        for record in _json_records(stream):
            if seen["records"] >= limit:
                break
            seen["records"] += 1
            for key in record:
                seen["record_keys"][key] = seen["record_keys"].get(key, 0) + 1
            if not _first_matching(record, _CODE_HINTS):
                seen["no_code"] += 1
                continue

            lists = []
            for key, value in record.items():
                folded = _norm_header(key)
                if isinstance(value, list) \
                        and not any(b in folded for b in _NOT_PERSON_LISTS) \
                        and any(h in folded for h in _PERSON_LIST_HINTS):
                    seen["list_keys"][key] = seen["list_keys"].get(key, 0) + 1
                    lists.append(value)
                    if not value:
                        seen["empty_person_list"] += 1
            if not lists:
                seen["no_person_list"] += 1
                continue

            for people in lists:
                for person in people:
                    if not isinstance(person, dict):
                        continue
                    seen["people"] += 1
                    for key in person:
                        seen["person_keys"][key] = \
                            seen["person_keys"].get(key, 0) + 1
                    if not person_name(person):
                        seen["no_name"] += 1
                        continue
                    if _first_matching(person, ("kehtivuse_lopp", "end_date",
                                                "lopp_kpv", "loppemise")):
                        seen["ended"] += 1
                        continue
                    seen["yielded"] += 1
        break
    return seen


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
            # Only the first records are needed to classify the file, and
            # reading further would mean parsing a gigabyte to answer a
            # yes/no question.
            for record, _ in zip(_json_records(stream), range(50)):
                if any(_person_lists(record)):
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
