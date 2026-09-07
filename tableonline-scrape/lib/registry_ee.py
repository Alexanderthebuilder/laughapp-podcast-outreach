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
        return None      # a company acting as board member, or a masked entry
    role = (row.get("role") or "").strip() or "juhatuse liige"
    return {"registrikood": code, "person_name": name, "role": role}


def is_active(status: str | None) -> bool:
    """Estonian status strings vary by release; anything naming deletion,
    liquidation or bankruptcy is treated as inactive."""
    s = strip_diacritics(status or "").lower()
    if not s:
        return True
    return not any(w in s for w in
                   ("kustutatud", "likvideerimisel", "pankrot", "loppenud",
                    "deleted", "liquidation", "bankrupt"))
