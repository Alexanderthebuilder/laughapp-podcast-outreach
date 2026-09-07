"""PRH / YTJ Opendata v3 record handling.

Field shapes verified against a live response and easy to get wrong:

  * `businessId` is an **object**, not a string: {"value": "3632327-4", ...}.
    Same for `euId`.
  * `names` is an array of RegisterName objects each with a `type`. Type "1" is
    the main name; parallel and auxiliary trade names live in the rest. Match
    against every entry — that is the mechanism that resolves the brand "Aoi"
    to "Ravintola Aoi Oy".
  * `addresses[]` uses `postCode` (capital C) and `co` (not `c/o`). Match the
    exact casing or the join silently returns nothing.
  * `/schema` returns OpenAPI YAML, not JSON.
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator

from .normalise import normalise_name, normalise_postcode, normalise_street

BASE = "https://avoindata.prh.fi/opendata-ytj-api/v3"
ALL_COMPANIES = f"{BASE}/all_companies"
DESCRIPTION = f"{BASE}/description"
POST_CODES = f"{BASE}/post_codes"

# TOL 2008. A helper for pulling likely targets out of the bulk file, never a
# gate: hotel restaurants register under 55101 and operating companies
# sometimes sit under a holding-company code, so a matched business ID keeps
# the row regardless of TOL.
TOL_CORE = {"56101"}                      # Ravintolat
TOL_SECONDARY = {"56102", "56301"}        # Kahvila-ravintolat, olut-/drinkkibaarit
TOL_MARGINAL = {"56302"}                  # Kahvilat — mostly noise
TOL_HOTEL = {"55101", "55102", "55201", "55901"}
# 56290 (staff and institutional canteens) is deliberately absent: not a
# reservation target.
TOL_TARGET = TOL_CORE | TOL_SECONDARY | TOL_MARGINAL | TOL_HOTEL

LANG_FI, LANG_SV, LANG_EN = "1", "2", "3"

# registeredEntries codes worth acting on.
EMPLOYER_REGISTER = ("41", "7")   # (type, register) — company has actual payroll
VAT_REGISTER = ("80", "6")

SITUATION_KEYWORDS = {
    "bankruptcy": ("konkurssi", "bankruptcy", "konkurs"),
    "liquidation": ("selvitystila", "liquidation", "likvidation"),
    "restructuring": ("yrityssaneeraus", "saneeraus", "restructuring",
                      "företagssanering"),
}
# Excluded from outreach entirely. Restructuring is a judgement call — they are
# cost-cutting, which cuts both ways against a success-fee incumbent — so it is
# flagged, not auto-dropped.
EXCLUDE_SITUATIONS = {"bankruptcy", "liquidation"}


def _s(v) -> str | None:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (int, float)):
        return str(v)
    return None


def business_id_of(rec: dict) -> str | None:
    """businessId is an object; a bare string is tolerated for robustness."""
    bid = rec.get("businessId")
    if isinstance(bid, dict):
        return _s(bid.get("value"))
    return _s(bid)


def description_for(node: dict, lang: str = LANG_FI) -> str | None:
    """Pick a description in the requested language from a descriptions[]."""
    descs = node.get("descriptions") if isinstance(node, dict) else None
    if not isinstance(descs, list):
        return None
    fallback = None
    for d in descs:
        if not isinstance(d, dict):
            continue
        text = _s(d.get("description"))
        if not text:
            continue
        if str(d.get("languageCode")) == lang:
            return text
        fallback = fallback or text
    return fallback


def names_of(rec: dict) -> list[dict]:
    """Every trade name with its type code, current entries first."""
    out = []
    for n in rec.get("names") or []:
        if not isinstance(n, dict):
            continue
        name = _s(n.get("name"))
        if not name:
            continue
        out.append({"name": name, "type": _s(n.get("type")),
                    "end_date": _s(n.get("endDate")),
                    "registration_date": _s(n.get("registrationDate"))})
    # A name with an endDate is historical; keep it (it still resolves a brand)
    # but rank current names first.
    out.sort(key=lambda n: (n["end_date"] is not None, n["type"] != "1"))
    return out


def primary_name(rec: dict) -> str | None:
    names = names_of(rec)
    return names[0]["name"] if names else None


def addresses_of(rec: dict) -> list[dict]:
    out = []
    for a in rec.get("addresses") or []:
        if not isinstance(a, dict):
            continue
        post_offices = a.get("postOffices") or []
        city = None
        if isinstance(post_offices, list):
            for po in post_offices:
                if isinstance(po, dict) and str(po.get("languageCode")) == LANG_FI:
                    city = _s(po.get("city"))
                    break
            if city is None:
                for po in post_offices:
                    if isinstance(po, dict):
                        city = _s(po.get("city"))
                        break
        out.append({
            "type": a.get("type"),
            "street": _s(a.get("street")),
            "building_number": _s(a.get("buildingNumber")),
            # postCode, capital C — verified live.
            "post_code": normalise_postcode(_s(a.get("postCode"))),
            "post_office": city,
            "co": _s(a.get("co")),          # `co`, not `c/o`
            "end_date": _s(a.get("endDate")),
        })
    # A visiting address (type 1) is a better venue signal than a postal
    # address (type 2), which is often the accountant's.
    out.sort(key=lambda a: (a["end_date"] is not None, a["type"] != 1))
    return out


def registered_flags(rec: dict) -> tuple[int, int]:
    """(in_employer_register, vat_liable).

    Employer-register membership means actual payroll, which cleanly separates
    real operating entities from holding shells and dormant companies.
    """
    employer = vat = 0
    for e in rec.get("registeredEntries") or []:
        if not isinstance(e, dict):
            continue
        pair = (_s(e.get("type")), _s(e.get("register")))
        status = _s(e.get("status"))
        # status "2" marks an ended entry in the observed data.
        if status == "2" or e.get("endDate"):
            continue
        if pair == EMPLOYER_REGISTER:
            employer = 1
        elif pair == VAT_REGISTER:
            vat = 1
    return employer, vat


def situation_flags(rec: dict) -> list[str]:
    """Restructuring / liquidation / bankruptcy. An empty array means clean."""
    flags: set[str] = set()
    for s in rec.get("companySituations") or []:
        if not isinstance(s, dict):
            continue
        blob = " ".join(filter(None, [
            _s(s.get("type")) or "", description_for(s, LANG_FI) or "",
            description_for(s, LANG_EN) or ""])).lower()
        for flag, words in SITUATION_KEYWORDS.items():
            if any(w in blob for w in words):
                flags.add(flag)
        if not flags and blob.strip():
            flags.add("other")
    return sorted(flags)


def is_ceased(rec: dict) -> bool:
    if _s(rec.get("endDate")):
        return True
    status = (_s(rec.get("tradeRegisterStatus")) or "").upper()
    return status in {"2", "CEASED", "LAKANNUT"}


def normalise_record(rec: dict) -> dict | None:
    """Flatten one PRH company into the companies_fi row shape."""
    bid = business_id_of(rec)
    if not bid:
        return None
    names = names_of(rec)
    addr = addresses_of(rec)
    first = addr[0] if addr else {}
    mbl = rec.get("mainBusinessLine") if isinstance(rec.get("mainBusinessLine"), dict) else {}
    employer, vat = registered_flags(rec)
    flags = situation_flags(rec)

    return {
        "business_id": bid,
        "primary_name": names[0]["name"] if names else None,
        "names_json": json.dumps(names, ensure_ascii=False),
        "main_business_line": _s(mbl.get("type")),
        "main_business_desc": description_for(mbl, LANG_FI),
        "company_forms": json.dumps(rec.get("companyForms") or [], ensure_ascii=False),
        "addresses_json": json.dumps(addr, ensure_ascii=False),
        "street": normalise_street(first.get("street")),
        "building_number": (first.get("building_number") or "").lower() or None,
        "post_code": first.get("post_code"),
        "post_office": first.get("post_office"),
        "website": _s(rec.get("website")),
        "registration_date": _s(rec.get("registrationDate")),
        "trade_register_status": _s(rec.get("tradeRegisterStatus")),
        "status": _s(rec.get("status")),
        "end_date": _s(rec.get("endDate")),
        "last_modified": _s(rec.get("lastModified")),
        "in_employer_register": employer,
        "vat_liable": vat,
        "situations_json": json.dumps(rec.get("companySituations") or [],
                                      ensure_ascii=False),
        "situation_flags": ",".join(flags) or None,
        "name_norm": normalise_name(names[0]["name"]) if names else None,
        "names_norm": "\n".join(sorted({normalise_name(n["name"]) for n in names
                                        if normalise_name(n["name"])})),
    }


def iter_companies(path) -> Iterator[dict]:
    """Stream companies out of the decompressed bulk JSON.

    The dump is roughly 96 MB zipped, so it is streamed rather than loaded
    whole. ijson is used where available; the fallback handles the file in one
    read, which needs a few GB of RAM and is only there so a missing optional
    dependency is not fatal.
    """
    import io

    def _from_obj(obj):
        if isinstance(obj, list):
            yield from (o for o in obj if isinstance(o, dict))
        elif isinstance(obj, dict):
            for key in ("companies", "results", "data", "items"):
                if isinstance(obj.get(key), list):
                    yield from (o for o in obj[key] if isinstance(o, dict))
                    return
            yield obj

    try:
        import ijson
    except ImportError:
        with open(path, "r", encoding="utf-8") as fh:
            yield from _from_obj(json.load(fh))
        return

    # Discover whether the array is at the root or under a wrapper key.
    with open(path, "rb") as fh:
        prefix = None
        for path_, event, _value in ijson.parse(fh):
            if event == "start_array":
                prefix = path_
                break
    item_prefix = f"{prefix}.item" if prefix else "item"
    with open(path, "rb") as fh:
        for obj in ijson.items(fh, item_prefix):
            if isinstance(obj, dict):
                yield obj


def tol_is_target(code: str | None) -> bool:
    return bool(code) and code in TOL_TARGET


def keep_record(rec_norm: dict, wanted_ids: Iterable[str],
                wanted_names: Iterable[str]) -> bool:
    """Whether a bulk record is worth storing locally.

    Keeps anything we already hold a business ID for (the primary-key route),
    anything in a target line of business, and anything whose trade name
    matches a restaurant name (the fallback route). Everything else is
    discarded so the local table stays small.
    """
    if rec_norm["business_id"] in wanted_ids:
        return True
    if tol_is_target(rec_norm["main_business_line"]):
        return True
    names = set((rec_norm["names_norm"] or "").split("\n"))
    return bool(names & set(wanted_names))
