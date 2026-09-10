"""Estonia publishes board members only as nested XML/JSON.

"Persons on registry card" carries its people inside each company record, and
the key names change between releases, so both the list and the fields inside
it are located by matching rather than by path.
"""
import json
from pathlib import Path

import pytest

from lib import registry_ee
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


# --- working out which downloaded file is which -----------------------------

@pytest.fixture()
def downloads(tmp_path):
    """What the register's download page actually gives you."""
    (tmp_path / "ettevotja_rekvisiidid.csv").write_text(
        "ariregistri_kood;nimi;ettevotja_staatus_tekstina;indeks\n"
        + "".join(f"1234567{i};Firma {i} OÜ;Registrisse kantud;1014{i}\n"
                 for i in range(5)), encoding="utf-8")
    (tmp_path / "kaardile_kantud_isikud.json").write_text(
        json.dumps(NESTED, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "yldandmed.xml").write_text(
        '<?xml version="1.0"?><root/>', encoding="utf-8")
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return tmp_path


def test_the_two_files_are_told_apart(downloads):
    """Both carry a registry code and a name, so only structure separates
    them: the board file nests its people."""
    from lib.registry_ee import discover
    found = discover(downloads)
    assert found["companies"].name == "ettevotja_rekvisiidid.csv"
    assert found["board"].name == "kaardile_kantud_isikud.json"


def test_xml_is_reported_rather_than_silently_ignored(downloads):
    from lib.registry_ee import discover
    assert [p.name for p in discover(downloads)["xml"]] == ["yldandmed.xml"]


def test_an_unrelated_file_is_listed_not_guessed_at(downloads):
    from lib.registry_ee import discover
    assert [p.name for p in discover(downloads)["unknown"]] == ["notes.csv"]


def test_an_empty_directory_finds_nothing(tmp_path):
    from lib.registry_ee import discover
    found = discover(tmp_path)
    assert found["companies"] is None and found["board"] is None


def test_a_company_on_the_board_is_not_a_person():
    """Legal entities sit on Estonian boards; they are not someone to email."""
    for name in ["Firma 0 OÜ", "Holding AS", "Ravintola Oy", "Something MTÜ"]:
        assert normalise_board_member(
            {"registrikood": "12345678", "person_name": name}) is None


def test_a_real_person_still_passes():
    got = normalise_board_member(
        {"registrikood": "12345678", "person_name": "Mari Tamm"})
    assert got["person_name"] == "Mari Tamm"


def _big_person_file(path, records=20000):
    """A file large enough that reading all of it is obvious in a byte count."""
    import json as _json
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"ettevotjad":[')
        for i in range(records):
            if i:
                fh.write(",")
            fh.write(_json.dumps({
                "ariregistri_kood": 10000000 + i,
                "nimi": f"Naide {i} OU",
                "kaardile_kantud_isikud": [
                    {"eesnimi": "Mari", "nimi": f"Tamm{i}",
                     "isiku_tyyp": "juhatuse liige"}],
            }, ensure_ascii=False))
        fh.write("]}")
    return path


def test_classifying_a_large_file_does_not_read_all_of_it(tmp_path, monkeypatch):
    """has_nested_people answers a yes/no question. Parsing a gigabyte to do
    it is what took the box down: json.load built the whole document first."""
    path = _big_person_file(tmp_path / "people.json")
    size = path.stat().st_size
    assert size > 2_000_000, "fixture too small to prove anything"

    read = []
    real_open = Path.open

    def counting_open(self, *a, **kw):
        fh = real_open(self, *a, **kw)
        if "b" in (a[0] if a else kw.get("mode", "r")):
            inner_read = fh.read

            def tracked(n=-1):
                chunk = inner_read(n)
                read.append(len(chunk))
                return chunk
            fh.read = tracked
        return fh

    monkeypatch.setattr(Path, "open", counting_open)
    assert registry_ee.has_nested_people(path) is True
    assert sum(read) < size / 4, (
        f"read {sum(read)} of {size} bytes to classify the file")


def test_a_large_file_yields_every_person(tmp_path):
    """Streaming must not silently truncate: the whole file still comes out."""
    path = _big_person_file(tmp_path / "people.json", records=5000)
    rows = list(registry_ee.iter_person_rows(path))
    assert len(rows) == 5000
    assert rows[0]["registrikood"] == "10000000"
    assert rows[-1]["registrikood"] == "10004999"


def test_a_bare_top_level_array_works_too(tmp_path):
    """The register publishes both shapes; the wrapping key is found by
    walking events, not guessed from a list of names."""
    import json as _json
    path = tmp_path / "flat.json"
    path.write_text(_json.dumps([
        {"ariregistri_kood": 12345678, "nimi": "Naide OU",
         "kaardile_kantud_isikud": [{"eesnimi": "Jaan", "nimi": "Kask"}]},
    ]), encoding="utf-8")

    rows = list(registry_ee.iter_person_rows(path))
    assert len(rows) == 1 and rows[0]["registrikood"] == "12345678"


def test_the_inspector_names_where_rows_are_lost(tmp_path):
    """A loader that drops records at four points and reports none of them
    makes "the key names changed" indistinguishable from "everyone looks
    resigned"."""
    path = tmp_path / "people.json"
    path.write_text(json.dumps([
        # kept
        {"ariregistri_kood": 1, "kaardile_kantud_isikud": [
            {"eesnimi": "Mari", "nimi": "Tamm", "lopp_kpv": None}]},
        # dropped: the person has left the board
        {"ariregistri_kood": 2, "kaardile_kantud_isikud": [
            {"eesnimi": "Jaan", "nimi": "Kask", "lopp_kpv": "01.01.2020"}]},
        # dropped: no key matches a name hint
        {"ariregistri_kood": 3, "kaardile_kantud_isikud": [
            {"tundmatu_valja": "x"}]},
        # dropped: no list whose key looks like people
        {"ariregistri_kood": 4, "midagi_muud": [{"eesnimi": "Peeter"}]},
        # dropped: no registry code at all
        {"nimi": "Kood Puudub OU"},
    ]), encoding="utf-8")

    got = registry_ee.inspect_person_file(path)
    assert got["records"] == 5
    assert got["no_code"] == 1
    assert got["no_person_list"] == 1
    assert got["people"] == 3
    assert got["no_name"] == 1
    assert got["ended"] == 1
    assert got["yielded"] == 1
    assert got["list_keys"]["kaardile_kantud_isikud"] == 3


def test_the_inspector_reports_an_empty_person_list_separately(tmp_path):
    """A company filed with no one on the card is not the same failure as a
    company whose people could not be read."""
    path = tmp_path / "people.json"
    path.write_text(json.dumps([
        {"ariregistri_kood": 1, "kaardile_kantud_isikud": []},
    ]), encoding="utf-8")

    got = registry_ee.inspect_person_file(path)
    assert got["empty_person_list"] == 1 and got["yielded"] == 0
