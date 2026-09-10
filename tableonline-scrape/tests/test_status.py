"""The one-screen summary must survive every shape the database takes.

It is the first thing run after a session times out, so it has to work on an
empty database, on a half-finished run, and on a complete one — reporting
"not started" rather than raising.
"""
import pytest

from lib.db import connect, init_db, now, upsert
from run_pipeline import run, write_registry_fixtures
from src import status


def _empty(tmp_path):
    conn = connect(str(tmp_path / "t.sqlite"))
    init_db(conn)
    return conn


def test_an_empty_database_says_so_rather_than_dividing_by_zero(tmp_path, capsys):
    status.report(_empty(tmp_path))
    assert "Phase 1 has not run" in capsys.readouterr().out


def test_restaurants_but_no_crawl_yet(tmp_path, capsys):
    conn = _empty(tmp_path)
    upsert(conn, "restaurants", {"tableonline_id": 1},
           {"name": "Testi", "country": "FI"})
    conn.commit()

    status.report(conn)
    out = capsys.readouterr().out
    assert "RESTAURANTS  1" in out
    assert "nothing left unjudged" in out          # no contacts, so none pending
    assert "phase5_registry ee-load" in out        # and Estonia not loaded


@pytest.fixture(scope="module")
def crawled(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("status") / "t.sqlite")
    conn = connect(db)
    init_db(conn)
    conn.close()
    run(db, write_registry_fixtures(tmp_path_factory.mktemp("reg")))
    return connect(db)


def test_a_full_run_reports_every_section(crawled, capsys):
    status.report(crawled)
    out = capsys.readouterr().out
    for heading in ("RESTAURANTS", "COVERAGE", "CONTACTS", "NAME JUDGING"):
        assert heading in out


def test_unjudged_counts_names_the_sheet_never_showed(crawled, capsys):
    """A name buried behind a better contact still has to be counted: clearing
    the one above it promotes this one into the sheet."""
    rid = crawled.execute("SELECT tableonline_id FROM restaurants").fetchone()[0]
    crawled.execute(
        "INSERT OR IGNORE INTO contacts (restaurant_id, contact_name, source,"
        " confidence, dedupe_key, found_at) VALUES (?,?,?,?,?,?)",
        (rid, "Aukioloajat Ma", "website_other", "low", "unjudged-probe", now()))
    crawled.commit()

    status.report(crawled)
    assert "still unjudged" in capsys.readouterr().out

    crawled.execute("DELETE FROM contacts WHERE dedupe_key='unjudged-probe'")
    crawled.commit()
