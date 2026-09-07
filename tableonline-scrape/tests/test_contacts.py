"""Contact identity. One human must not become two Pipedrive Persons."""
import pytest

from lib.db import add_contact, connect, init_db
from lib.patterns import candidates, ordered_candidates, split_name


@pytest.fixture()
def conn():
    c = connect(":memory:")
    init_db(c)
    c.execute("INSERT INTO restaurants (tableonline_id, name) VALUES (1, 'Aoi')")
    return c


def rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT email, contact_name, contact_role, source, dedupe_key"
        " FROM contacts ORDER BY id")]


def test_name_first_then_email_merges_and_moves_provenance(conn):
    add_contact(conn, 1, email=None, contact_name="Matti Virtanen",
                contact_role=None, source="website_other", source_url="u",
                confidence="low")
    add_contact(conn, 1, email="matti.virtanen@aoi.fi", contact_name="Matti Virtanen",
                contact_role="omistaja", source="website_privacy",
                source_url="p", confidence="high")
    assert rows(conn) == [{"email": "matti.virtanen@aoi.fi",
                           "contact_name": "Matti Virtanen",
                           "contact_role": "omistaja",
                           "source": "website_privacy",
                           "dedupe_key": "matti.virtanen@aoi.fi"}]


def test_email_first_then_bare_name_does_not_duplicate(conn):
    add_contact(conn, 1, email="mari@aoi.fi", contact_name="Mari Tamm",
                contact_role=None, source="website_contact", source_url="c",
                confidence="high")
    add_contact(conn, 1, email=None, contact_name="Mari Tamm",
                contact_role="juhatuse liige", source="registry_ee",
                source_url=None, confidence="medium")
    assert len(rows(conn)) == 1
    assert rows(conn)[0]["contact_role"] == "juhatuse liige"


def test_email_dedupe_is_case_insensitive(conn):
    add_contact(conn, 1, email="Info@Aoi.fi", contact_name=None, contact_role=None,
                source="website_contact", source_url="c", confidence="high")
    add_contact(conn, 1, email="info@aoi.fi", contact_name=None, contact_role=None,
                source="website_other", source_url="o", confidence="medium")
    assert len(rows(conn)) == 1


def test_a_second_address_for_the_same_person_stays_separate(conn):
    add_contact(conn, 1, email="mari.tamm@aoi.fi", contact_name="Mari Tamm",
                contact_role=None, source="website_privacy", source_url="p",
                confidence="high")
    add_contact(conn, 1, email="mari@aoi.fi", contact_name="Mari Tamm",
                contact_role=None, source="website_contact", source_url="c",
                confidence="high")
    assert len(rows(conn)) == 2


def test_contact_with_neither_email_nor_name_is_refused(conn):
    assert not add_contact(conn, 1, email=None, contact_name=None,
                           contact_role="owner", source="x", source_url=None,
                           confidence="low")


def test_pattern_candidates_fold_diacritics():
    assert candidates("Mari-Liis Tõnisson", "kohvik.ee")[1] == \
        "mariliis.tonisson@kohvik.ee"
    assert candidates("Åsa Öhman", "x.fi")[0] == "asa@x.fi"


def test_single_name_yields_no_candidates():
    assert candidates("Madonna", "x.fi") == []
    assert split_name("Madonna") is None


def test_observed_domain_pattern_is_tried_first():
    assert ordered_candidates("Liisa Koskinen", "aoi.fi",
                              ["matti.virtanen@aoi.fi"])[0] == "liisa.koskinen@aoi.fi"
    assert ordered_candidates("Liisa Koskinen", "aoi.fi",
                              ["m.virtanen@aoi.fi"])[0] == "l.koskinen@aoi.fi"
