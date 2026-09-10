"""The contact sheet itself — the deliverable."""
import csv
from pathlib import Path

import pytest

from lib.db import connect, init_db
from run_pipeline import run, write_registry_fixtures
from src.export_sheet import first_name


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("sheet") / "t.sqlite")
    conn = connect(db)
    init_db(conn)
    conn.close()
    run(db, write_registry_fixtures(tmp_path_factory.mktemp("reg")))

    import src.export_sheet as ex
    out = tmp_path_factory.mktemp("out")
    ex.main(["--db", db, "--name", "sheet_test"])
    path = Path(__file__).resolve().parent.parent / "exports" / "sheet_test.csv"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    return rows


def test_first_name_is_the_greeting_name():
    assert first_name("Mari-Liis Tamm") == "Mari-Liis"
    assert first_name("Matti Virtanen") == "Matti"
    assert first_name(None) == ""


def test_the_five_requested_columns_come_first(sheet):
    assert list(sheet[0])[:6] == ["Restaurant", "First name", "Email", "Area",
                                  "Country", "Phone"]


def test_area_and_country_are_human_readable(sheet):
    by_name = {r["Restaurant"]: r for r in sheet}
    assert by_name["Elevant"]["Area"] == "Tallinn"
    assert by_name["Elevant"]["Country"] == "Estonia"
    assert by_name["Restaurant Aoi"]["Country"] == "Finland"


def test_phone_comes_from_google_when_the_listing_has_none(sheet):
    by_name = {r["Restaurant"]: r for r in sheet}
    assert by_name["Restaurant Aoi"]["Phone"] == "+358 9 6128 5100"


def test_permanently_closed_venue_is_not_in_the_sheet(sheet):
    assert "Vegan Restoran V" not in {r["Restaurant"] for r in sheet}


def test_guessed_address_is_labelled_and_carries_a_confirmed_backup(sheet):
    """A guess may bounce; the shared inbox must still be reachable."""
    elevant = next(r for r in sheet if r["Restaurant"] == "Elevant")
    assert elevant["Email type"] == "guessed"
    assert elevant["Backup email"] == "info@elevant.ee"


def test_harvested_personal_address_is_labelled_personal(sheet):
    aoi = next(r for r in sheet if r["Restaurant"] == "Restaurant Aoi")
    assert aoi["Email type"] == "personal"
    assert aoi["First name"] == "Matti" and aoi["Role"] == "owner"


def test_a_named_contact_without_an_address_does_not_blank_the_email(tmp_path):
    """A trade register extract names the managing director and carries no
    email for them. Reading the name and the address off one "best contact"
    let that row win and emptied the email column for restaurants that had
    a perfectly good address on another row."""
    import src.export_sheet as ex
    from lib.db import connect, init_db, now

    db = tmp_path / "t.sqlite"
    conn = connect(str(db))
    init_db(conn)
    conn.execute("INSERT INTO restaurants (tableonline_id, name, country, city)"
                 " VALUES (1,'Testi','FI','Helsinki')")
    # Ranks first: has a name, high confidence — and no address.
    conn.execute(
        "INSERT INTO contacts (restaurant_id, contact_name, contact_role,"
        " source, confidence, dedupe_key, found_at)"
        " VALUES (1,'Matti Virtanen','managing director','registry_fi',"
        "'high','virre:Matti Virtanen',?)", (now(),))
    # Ranks second: no name, lower confidence — and the working address.
    conn.execute(
        "INSERT INTO contacts (restaurant_id, email, source, confidence,"
        " dedupe_key, found_at)"
        " VALUES (1,'info@testi.fi','website_contact','medium','info@testi.fi',?)",
        (now(),))
    conn.commit()
    conn.close()

    ex.main(["--db", str(db), "--name", "sheet_both"])
    path = Path(__file__).resolve().parent.parent / "exports" / "sheet_both.csv"
    row = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))[0]

    assert row["Email"] == "info@testi.fi"       # taken from the second row
    assert row["First name"] == "Matti"          # name still from the first
    assert row["Full name"] == "Matti Virtanen"
    assert row["Role"] == "managing director"
