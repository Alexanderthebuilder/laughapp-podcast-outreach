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
