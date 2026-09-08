"""Re-applying the country mapping must not cost another 40-minute sweep,
and must not erase unrelated review reasons."""
import sqlite3
import tempfile

import pytest

import src.phase1_enumerate as p1
from lib.cities import country_for_slug
from lib.db import connect, init_db


@pytest.fixture()
def db():
    path = tempfile.mktemp(suffix=".sqlite")
    conn = connect(path)
    init_db(conn)
    rows = [
        (1, "helsinki", "FI", 0, None),
        (2, "levi", None, 1, "unmapped city slug 'levi'"),
        (3, "saaremaa", None, 1, "unmapped city slug 'saaremaa'"),
        (4, "levi", None, 1, "image_id 309 != tableonline_id 4 (merged or stale "
                             "record); unmapped city slug 'levi'"),
        (5, "atlantis-city", None, 1, "unmapped city slug 'atlantis-city'"),
    ]
    for tid, slug, country, review, reason in rows:
        conn.execute(
            "INSERT INTO restaurants (tableonline_id, city_slug, country,"
            " needs_review, review_reason) VALUES (?,?,?,?,?)",
            (tid, slug, country, review, reason))
    conn.commit()
    conn.close()
    p1.main(["--db", path, "--no-report", "recountry"])
    return path


def fetch(path, tid):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return dict(conn.execute(
        "SELECT * FROM restaurants WHERE tableonline_id=?", (tid,)).fetchone())


def test_a_finnish_resort_slug_resolves(db):
    row = fetch(db, 2)
    assert row["country"] == "FI" and row["city"] == "Levi"
    assert row["needs_review"] == 0 and row["review_reason"] is None


def test_an_estonian_island_resolves_to_ee_not_fi(db):
    assert fetch(db, 3)["country"] == "EE"


def test_an_unrelated_review_reason_survives(db):
    row = fetch(db, 4)
    assert row["country"] == "FI"
    assert row["needs_review"] == 1
    assert "image_id 309" in row["review_reason"]
    assert "unmapped" not in row["review_reason"]


def test_a_genuinely_unknown_slug_is_still_not_defaulted(db):
    row = fetch(db, 5)
    assert row["country"] is None and row["needs_review"] == 1


def test_ee_and_fi_slug_sets_do_not_overlap():
    from lib.cities import EE_SLUGS, FI_SEED_SLUGS
    assert not (EE_SLUGS & FI_SEED_SLUGS)


@pytest.mark.parametrize("slug,expected", [
    ("levi", "FI"), ("yllas", "FI"), ("kittila", "FI"), ("tampere", "FI"),
    ("saaremaa", "EE"), ("parnu", "EE"), ("narva", "EE"), ("viimsi", "EE"),
    ("atlantis-city", None),
])
def test_slug_classification(slug, expected):
    assert country_for_slug(slug)[0] == expected
