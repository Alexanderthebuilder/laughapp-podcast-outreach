"""Estonia publishes board members only as nested XML/JSON.

"Persons on registry card" carries its people inside each company record, and
the key names change between releases, so both the list and the fields inside
it are located by matching rather than by path.
"""
import json

import pytest

from lib.registry_ee import iter_person_rows, normalise_board_member

NESTED = [
    {"ariregistri_kood": "12345678", "nimi": "Elevant OÜ",
     "kaardile_kantud_isikud": [
         {"isiku_tyyp": "F", "nimi_arinimi": "Mari Tamm",
          "isiku_roll": "juhatuse liige", "algus_kpv": "2019-01-01"},
         {"isiku_tyyp": "F", "nimi_arinimi": "Jaan Kask",
          "isiku_roll": "juhatuse liige"},
         {"isiku_tyyp": "F", "nimi_arinimi": "Endine Liige",
          "isiku_roll": "juhatuse liige", "kehtivuse_lopp": "2022-05-01"}]},
    {"ariregistri_kood": "87654321", "nimi": "Kohvik OÜ",
     "kaardile_kantud_isikud": [
         {"isiku_tyyp": "F", "nimi_arinimi": "Silver Karuks",
          "isiku_roll": "juhataja"}]},
]


@pytest.fixture()
def nested(tmp_path):
    p = tmp_path / "persons.json"
    p.write_text(json.dumps(NESTED, ensure_ascii=False), encoding="utf-8")
    return p


def test_people_are_lifted_out_of_their_company_record(nested):
    got = {(r["registrikood"], r["person_name"]) for r in iter_person_rows(nested)}
    assert ("12345678", "Mari Tamm") in got
    assert ("87654321", "Silver Karuks") in got


def test_a_departed_board_member_is_skipped(nested):
    """An end date means they have left; emailing them helps nobody."""
    names = {r["person_name"] for r in iter_person_rows(nested)}
    assert "Endine Liige" not in names
    assert len(names) == 3


def test_the_role_is_carried_through(nested):
    roles = {r["person_name"]: r["role"] for r in iter_person_rows(nested)}
    assert roles["Mari Tamm"] == "juhatuse liige"
    assert roles["Silver Karuks"] == "juhataja"


def test_rows_survive_normalisation(nested):
    for row in iter_person_rows(nested):
        assert normalise_board_member(row) is not None


def test_a_wrapped_payload_is_unwrapped(tmp_path):
    """Some releases wrap the array in an object."""
    p = tmp_path / "wrapped.json"
    p.write_text(json.dumps({"ettevotjad": NESTED}, ensure_ascii=False),
                 encoding="utf-8")
    assert len(list(iter_person_rows(p))) == 3


def test_an_already_flat_record_still_works(tmp_path):
    """A future CSV release, or a flat JSON export, needs no change here."""
    p = tmp_path / "flat.json"
    p.write_text(json.dumps([
        {"ariregistri_kood": "12345678", "isiku_nimi": "Mari Tamm",
         "isiku_roll": "juhatuse liige"}], ensure_ascii=False), encoding="utf-8")
    rows = list(iter_person_rows(p))
    assert rows == [{"registrikood": "12345678", "person_name": "Mari Tamm",
                     "role": "juhatuse liige"}]


def test_a_record_with_no_registry_code_is_skipped(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"nimi": "No Code OÜ",
                              "kaardile_kantud_isikud": [
                                  {"nimi_arinimi": "Someone"}]}]),
                 encoding="utf-8")
    assert list(iter_person_rows(p)) == []
