"""Full seven-phase run against fixtures, then a second run over the same
database. Acceptance criterion: re-runnable end to end without duplicates."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from lib.db import connect, init_db
from run_pipeline import run, write_registry_fixtures

TABLES = ("restaurants", "contacts", "business_ids", "registry_matches",
          "scores", "websites", "website_pages", "socials", "groups")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pipeline") / "t.sqlite")
    conn = connect(path)
    init_db(conn)
    conn.close()
    reg = write_registry_fixtures(tmp_path_factory.mktemp("reg"))
    run(path, reg)
    return path


def counts(path):
    c = sqlite3.connect(path)
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def q(path, sql, params=()):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return [dict(r) for r in c.execute(sql, params)]


def test_active_listings_collected_and_merged_record_refused(db):
    ids = {r["tableonline_id"] for r in q(db, "SELECT tableonline_id FROM restaurants")}
    assert ids == {92, 1204, 1495, 1528}
    assert 635 not in ids       # empty og:title, never ingested as a lead


def test_country_derived_from_city_slug(db):
    got = {r["name"]: r["country"] for r in
           q(db, "SELECT name, country FROM restaurants")}
    assert got["Elevant"] == "EE" and got["Restaurant Aoi"] == "FI"
    assert not q(db, "SELECT 1 FROM restaurants WHERE country IS NULL")


def test_tenure_buckets_are_quartiled_lowest_id_first(db):
    got = {r["tableonline_id"]: r["tenure_bucket"] for r in
           q(db, "SELECT tableonline_id, tenure_bucket FROM restaurants")}
    assert got[92] == 1 and got[1528] == 4


def test_business_ids_are_validated_not_merely_matched(db):
    got = {r["business_id"]: r["confidence"] for r in
           q(db, "SELECT business_id, confidence FROM business_ids")}
    assert got["3632327-4"] == "checksum_valid"
    assert got["12345678"] == "keyword_corroborated"


def test_registry_join_prefers_the_primary_key(db):
    got = {r["restaurant_id"]: r["match_method"] for r in
           q(db, "SELECT restaurant_id, match_method FROM registry_matches")}
    assert got[1528] == "business_id"      # Y-tunnus harvested from its own site
    assert got[1204] == "name"             # no ID on site, fell back to the name
    assert got[1495] == "none"             # unmatched, kept for review


def test_unmatched_rows_are_flagged_never_dropped(db):
    assert q(db, "SELECT 1 FROM registry_matches WHERE restaurant_id=1495"
                 " AND needs_review=1")


def test_named_contact_carries_a_role_and_a_clean_name(db):
    rows = q(db, "SELECT contact_name, contact_role, source FROM contacts"
                 " WHERE restaurant_id=1528 AND contact_name IS NOT NULL")
    assert rows == [{"contact_name": "Matti Virtanen", "contact_role": "owner",
                     "source": "website_privacy"}]


def test_privacy_page_produced_the_named_owner(db):
    """The whole reason privacy pages are crawled before contact pages."""
    assert q(db, "SELECT 1 FROM contacts WHERE source='website_privacy'"
                 " AND contact_name IS NOT NULL")


def test_estonian_board_members_become_named_contacts(db):
    names = {r["contact_name"] for r in
             q(db, "SELECT contact_name FROM contacts WHERE restaurant_id=92"
                   " AND contact_name IS NOT NULL")}
    assert names == {"Mari Tamm", "Jaan Kask"}


def test_shared_inboxes_are_never_attributed_to_a_person(db):
    for r in q(db, "SELECT email, contact_name FROM contacts"
                   " WHERE email LIKE 'info@%'"):
        assert r["contact_name"] is None


def test_permanently_closed_venue_is_kept_but_excluded_from_outreach(db):
    assert q(db, "SELECT 1 FROM places WHERE restaurant_id=1495"
                 " AND business_status='CLOSED_PERMANENTLY'")
    csv_path = Path(__file__).resolve().parent.parent / "exports" / \
        "tableonline_attack_list.csv"
    assert "Vegan Restoran V" not in csv_path.read_text(encoding="utf-8")


def test_pipedrive_payload_puts_people_and_inboxes_in_the_right_place(db):
    import json
    path = Path(__file__).resolve().parent.parent / "exports" / \
        "pipedrive_payloads.jsonl"
    payloads = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elevant = next(p for p in payloads if p["restaurant_id"] == 92)
    assert {p["name"] for p in elevant["persons"]} == {"Mari Tamm", "Jaan Kask"}
    assert elevant["org_emails"] == ["info@elevant.ee"]
    assert elevant["label"] == "TableOnline Attack List"


def test_run_report_is_written(db):
    report = Path(__file__).resolve().parent.parent / "run_report.md"
    text = report.read_text(encoding="utf-8")
    assert "Acceptance criteria" in text and "Yield per source" in text


def test_second_full_run_adds_no_duplicates(db, tmp_path_factory):
    before = counts(db)
    run(db, write_registry_fixtures(tmp_path_factory.mktemp("reg2")))
    assert counts(db) == before
    dupes = q(db, "SELECT restaurant_id, dedupe_key, COUNT(*) n FROM contacts"
                  " GROUP BY 1,2 HAVING n > 1")
    assert dupes == []
