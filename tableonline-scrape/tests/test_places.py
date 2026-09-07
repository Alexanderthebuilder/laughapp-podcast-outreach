"""Phase 3 accept rules. A wrong website poisons every downstream phase."""
import sqlite3

from src.phase3_places import build_queries, score_candidate


def row(**kw):
    base = {"tableonline_id": 1, "name": "Restaurant Aoi", "city": "Helsinki",
            "city_slug": "helsinki", "country": "FI",
            "street_address": "Kalevankatu 3", "postal_code": "00100",
            "lat": None, "lng": None}
    base.update(kw)
    return base


def place(name, lat=None, lng=None, addr=""):
    p = {"displayName": {"text": name}, "formattedAddress": addr}
    if lat is not None:
        p["location"] = {"latitude": lat, "longitude": lng}
    return p


def test_query_falls_back_from_full_address_to_name_and_city():
    qs = build_queries(row())
    assert qs[0] == "Restaurant Aoi, Kalevankatu 3, Helsinki, Finland"
    assert qs[1] == "Restaurant Aoi, Helsinki, Finland"
    assert build_queries(row(street_address=None)) == ["Restaurant Aoi, Helsinki, Finland"]


def test_name_similarity_alone_accepts():
    r = score_candidate(row(lat=None, lng=None), place("Ravintola Aoi"))
    assert r["accepted"] and "name" in r["method"]


def test_proximity_accepts_a_renamed_venue_at_the_same_address():
    """The 150 m rule exists precisely for a venue that changed its name."""
    r = score_candidate(row(name="Roof", lat=59.4371, lng=24.7537),
                        place("Katusekohvik", 59.4370, 24.7536))
    assert r["accepted"] and r["method"] == "distance" and r["distance_m"] < 150


def test_distant_low_similarity_match_is_refused():
    r = score_candidate(row(name="Ghost", lat=60.1675, lng=24.9385),
                        place("Totally Different Bistro", 60.9, 25.9))
    assert not r["accepted"] and r["method"] == "none"


def test_address_agreement_substitutes_when_we_have_no_coordinates():
    r = score_candidate(row(name="Zzz"),
                        place("Qqq", addr="Kalevankatu 3, 00100 Helsinki"))
    assert r["accepted"] and r["method"] == "address"


def test_address_with_a_different_postcode_does_not_agree():
    r = score_candidate(row(name="Zzz"),
                        place("Qqq", addr="Kalevankatu 3, 00999 Helsinki"))
    assert not r["accepted"]
