"""Parsing a trade register extract.

The fixture mirrors the layout of a real extract exactly — section order,
the "Tehtävä tai asema / Nimi / Syntymäaika" table, the auditor block, the
registered contact lines — with invented people, because this repository is
public and the real extract names private individuals.
"""
import pytest

from lib.virre import (EXCLUDED_ROLES, business_id, officers, parse_extract,
                       registered_contact)

EXTRACT = """Sivu 1/3
Y-tunnus: 1234567-8
OTE 10.09.2026
Ote kaupparekisteristä
Yritys
Toiminimi Esimerkki Ravintolat Oy
Y-tunnus 1234567-8
Yritys rekisteröity 28.07.1993
Yritysmuoto Osakeyhtiö
Yhteystiedot
Postiosoite Kauppatori 4
78250 VARKAUS
Puhelin 017597011
Faksi 0175527994
Sähköposti ravintola@esimerkki.fi
www-osoite www.esimerkki.fi
Rekisterimerkinnät
Pääomatiedot
Osakepääoma 60 000,00 EUR
Hallitus
Hallitus (Rekisteröity 17.03.2026 11:05:04)
Tehtävä tai asema Nimi Syntymäaika
Puheenjohtaja Lahtinen Olli-Pekka 17.07.1978
Jäsen Lahtinen Jaakko Ilmari 08.11.1996
Jäsen Salo Riikka Maria 13.03.1981
Varajäsen Koskinen Eero 02.02.1990
Toimitusjohtaja
Toimitusjohtaja (Rekisteröity 09.01.2020 08:20:39)
Tehtävä tai asema Nimi Syntymäaika
Toimitusjohtaja Salo Riikka Maria 13.03.1981
Tilintarkastajat
Tilintarkastajat (Rekisteröity 09.01.2020 08:20:39)
Tehtävä tai asema Nimi Syntymäaika
Tilintarkastaja Esimerkki Tilintarkastus Oy, Y-tunnus
2204039-6, Kaupparekisteri
Päävastuullinen tilintarkastaja Virtanen Mervi Susanna 13.08.1970
Edustaminen
Lakimääräinen edustaminen Toiminimen kirjoittaa osakeyhtiölain nojalla hallitus.
Voimassa olevat henkilötiedot
Nimi Syntymäaika Kansalaisuus Kotipaikka
Virtanen Mervi Susanna 13.08.1970 Suomen kansalainen Kuopio
Salo Riikka Maria 13.03.1981 Suomen kansalainen Espoo
Tietolähde: Patentti- ja rekisterihallitus
"""


def test_names_are_flipped_out_of_surname_first_order():
    """The register prints "Salo Riikka Maria". Greeting her "Hi Salo" is
    worse than not greeting her at all."""
    by_name = {o.name: o for o in officers(EXTRACT)}
    assert "Riikka Maria Salo" in by_name
    assert by_name["Riikka Maria Salo"].given == "Riikka Maria"
    assert by_name["Riikka Maria Salo"].surname == "Salo"


def test_the_managing_director_outranks_the_board():
    assert officers(EXTRACT)[0].name == "Riikka Maria Salo"
    assert officers(EXTRACT)[0].role == "toimitusjohtaja"


def test_a_person_holding_two_roles_appears_once_at_the_better_one():
    """Riikka Maria Salo is both a board member and managing director."""
    names = [o.name for o in officers(EXTRACT)]
    assert names.count("Riikka Maria Salo") == 1


def test_auditors_are_excluded():
    names = [o.name for o in officers(EXTRACT)]
    assert not any("Virtanen" in n for n in names)      # lead auditor
    assert not any("Tilintarkastus" in n for n in names)  # the audit firm


def test_an_audit_firm_is_never_read_as_a_person():
    """It sits in the same table as the directors and carries its own
    business ID, so only the company markers tell it apart."""
    assert not any("Esimerkki" in o.name for o in officers(EXTRACT))


def test_the_longer_auditor_role_is_not_read_as_the_shorter_one():
    """"Päävastuullinen tilintarkastaja" must not match as "tilintarkastaja"
    with "Päävastuullinen" swallowed into the name."""
    assert "paavastuullinen tilintarkastaja" in EXCLUDED_ROLES
    assert not any(o.surname == "Päävastuullinen" for o in officers(EXTRACT))


def test_no_birth_date_survives_parsing():
    """Discarded at parse time, not stored and filtered later."""
    blob = repr(parse_extract(EXTRACT))
    for date in ("17.07.1978", "13.03.1981", "08.11.1996", "13.08.1970"):
        assert date not in blob


def test_the_registered_contact_block_is_read():
    got = registered_contact(EXTRACT)
    assert got["email"] == "ravintola@esimerkki.fi"
    assert got["phone"] == "017597011"
    assert got["website"] == "www.esimerkki.fi"


def test_business_id_comes_off_the_header():
    assert business_id(EXTRACT) == "1234567-8"


def test_the_deputy_ranks_below_ordinary_members():
    ranked = [o.name for o in officers(EXTRACT)]
    assert ranked.index("Eero Koskinen") == len(ranked) - 1


@pytest.mark.parametrize("junk", [
    "", "Tehtävä tai asema Nimi Syntymäaika", "Osakepääoma 60 000,00 EUR",
    "Hallitus (Rekisteröity 17.03.2026 11:05:04)",
    "Jäsen Yksinimi 01.01.1980",        # one token is not a person
])
def test_table_furniture_is_not_mistaken_for_people(junk):
    assert officers(junk) == []


def test_an_extract_with_no_officers_parses_to_nothing():
    got = parse_extract("Y-tunnus: 1234567-8\nToiminimi Tyhja Oy\n")
    assert got["officers"] == [] and got["business_id"] == "1234567-8"


def test_only_a_real_pdf_is_saved(tmp_path):
    """A blob read that half-works returns bytes that are not a document.
    Saved unchecked, the failure surfaces at parse time as an unreadable file
    and the real cause is two steps away."""
    from src.phase5_virre import save_pdf

    good, bad = tmp_path / "good.pdf", tmp_path / "bad.pdf"
    assert save_pdf(good, b"%PDF-1.7\nbody") is True
    assert good.read_bytes().startswith(b"%PDF")

    assert save_pdf(bad, b"<!doctype html><html>error</html>") is False
    assert not bad.exists()


def _group_db(tmp_path):
    """One company running five restaurants — the shape that broke both ends."""
    from lib.db import connect, init_db, now
    conn = connect(str(tmp_path / "t.sqlite"))
    init_db(conn)
    for rid in range(1, 6):
        conn.execute("INSERT INTO restaurants (tableonline_id, name)"
                     " VALUES (?,?)", (rid, f"Group venue {rid}"))
        conn.execute(
            "INSERT INTO business_ids (restaurant_id, business_id, country,"
            " confidence, found_at) VALUES (?,?,?,?,?)",
            (rid, "2854694-5", "FI", "checksum_valid", now()))
    conn.execute("INSERT INTO restaurants (tableonline_id, name) VALUES (9,'Solo')")
    conn.execute(
        "INSERT INTO business_ids (restaurant_id, business_id, country,"
        " confidence, found_at) VALUES (9,'1234567-8','FI','checksum_valid',?)",
        (now(),))
    conn.commit()
    return conn


def test_a_group_is_fetched_once_not_once_per_venue(tmp_path, monkeypatch):
    """Five venues under one Y-tunnus asked PRH for the same document five
    times and wrote it to the same file, which defeats pacing the requests."""
    from src import phase5_virre

    monkeypatch.setattr(phase5_virre, "PDF_DIR", tmp_path / "pdfs")
    todo = phase5_virre.pending(_group_db(tmp_path), None)
    assert todo == ["1234567-8", "2854694-5"]


def test_an_extract_reaches_every_venue_in_the_group(tmp_path):
    """The owner an extract names is the contact for all of that group's
    venues. A dict keyed business_id -> restaurant_id keeps one and silently
    drops the other four."""
    from src.phase5_virre import restaurants_by_business_id

    got = restaurants_by_business_id(_group_db(tmp_path))
    assert sorted(got["2854694-5"]) == [1, 2, 3, 4, 5]
    assert got["1234567-8"] == [9]


def test_an_already_fetched_extract_is_not_fetched_again(tmp_path, monkeypatch):
    from src import phase5_virre

    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "2854694-5.pdf").write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(phase5_virre, "PDF_DIR", pdfs)

    assert phase5_virre.pending(_group_db(tmp_path), None) == ["1234567-8"]
