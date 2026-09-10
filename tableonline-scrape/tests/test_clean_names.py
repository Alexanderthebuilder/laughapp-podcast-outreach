"""Site furniture must never reach the First name column of a greeting."""
import pytest

from lib.db import add_contact, connect, init_db
from lib.emails import _looks_like_person
from src.clean_names import analyse, gather, group_owners

REAL_PEOPLE = ["Matti Virtanen", "Liisa Koskinen", "Anna Nurmi", "Mari Tamm",
               "Jaan Kask", "Pekka Salo", "Mari-Liis Tonisson"]
JUNK = ["Facebook", "Gift Cards", "Follow Instagram", "Happy Hour",
        "Wine List", "Opening Hours", "Privacy Policy", "Table Online"]


@pytest.mark.parametrize("name", REAL_PEOPLE)
def test_real_names_survive_the_vocabulary_check(name):
    assert _looks_like_person(name)


@pytest.mark.parametrize("name", JUNK)
def test_known_furniture_is_rejected_by_vocabulary(name):
    assert not _looks_like_person(name)


@pytest.fixture()
def conn():
    c = connect(":memory:")
    init_db(c)
    for tid, name, domain in [(1, "Ravintola Aoi", "aoi.fi"),
                              (2, "Bierstube", "bierstube.fi"),
                              (3, "Kitchen & Bar", "kitchenandbar.fi"),
                              (4, "Elevant", "elevant.ee")]:
        c.execute("INSERT INTO restaurants (tableonline_id, name) VALUES (?,?)",
                  (tid, name))
        c.execute("INSERT INTO websites (restaurant_id, domain) VALUES (?,?)",
                  (tid, domain))
    return c


def flagged_names(conn, min_sites=3):
    """Names the deterministic pass rejects outright."""
    rows = gather(conn)
    rejects, _suspects, _sites = analyse(rows, min_sites, group_owners(conn))
    return {r["contact_name"] for r in rows if r["id"] in rejects}


def suspect_names(conn, min_sites=3):
    """Names left for the model to judge — deleted by neither pass alone."""
    rows = gather(conn)
    _rejects, suspects, _sites = analyse(rows, min_sites, group_owners(conn))
    return {r["contact_name"] for r in rows if r["id"] in suspects}


def test_a_word_no_list_anticipated_becomes_suspect_not_a_deletion(conn):
    """Frequency raises suspicion but must not delete on its own — the same
    signal describes a restaurant group's owner."""
    for tid in (1, 2, 3, 4):
        add_contact(conn, tid, email=f"a{tid}@x.fi", contact_name="Zephyr Quux",
                    contact_role=None, source="website_other", source_url="u",
                    confidence="low")
    assert "Zephyr Quux" not in flagged_names(conn)
    assert "Zephyr Quux" in suspect_names(conn)


def test_a_group_owner_across_many_venues_is_never_rejected(conn):
    """The most valuable contact in the list looks exactly like site furniture
    to a frequency count. Deleting them would be the worst error available."""
    for tid in (1, 2, 3, 4):
        add_contact(conn, tid, email=f"matti{tid}@x.fi",
                    contact_name="Matti Virtanen", contact_role="toimitusjohtaja",
                    source="website_privacy", source_url="p", confidence="high")
    assert "Matti Virtanen" not in flagged_names(conn)


def test_shared_company_corroborates_a_group_owner(conn):
    for tid in (1, 2, 3, 4):
        add_contact(conn, tid, email=f"matti{tid}@x.fi",
                    contact_name="Matti Virtanen", contact_role="toimitusjohtaja",
                    source="website_privacy", source_url="p", confidence="high")
        conn.execute("INSERT INTO registry_matches (restaurant_id, business_id,"
                     " country) VALUES (?,'2617416-4','FI')", (tid,))
    rows = gather(conn)
    _rejects, suspects, _sites = analyse(rows, 3, group_owners(conn))
    reason = " ".join(next(iter(suspects.values())))
    assert "group owner" in reason and "2617416-4" in reason


def test_furniture_at_many_venues_is_still_rejected_outright(conn):
    """Vocabulary still rejects, whatever the frequency says."""
    for tid in (1, 2, 3, 4):
        add_contact(conn, tid, email=f"g{tid}@x.fi", contact_name="Gift Cards",
                    contact_role=None, source="website_other", source_url="u",
                    confidence="low")
    assert "Gift Cards" in flagged_names(conn)


def test_a_real_person_at_one_restaurant_is_kept(conn):
    add_contact(conn, 1, email="m@aoi.fi", contact_name="Matti Virtanen",
                contact_role="owner", source="website_privacy", source_url="p",
                confidence="high")
    assert "Matti Virtanen" not in flagged_names(conn)


def test_the_same_person_at_two_venues_is_kept(conn):
    """A small group can share an owner; two venues is not suspicious."""
    for tid in (1, 2):
        add_contact(conn, tid, email=f"m{tid}@x.fi", contact_name="Matti Virtanen",
                    contact_role="owner", source="website_privacy",
                    source_url="p", confidence="high")
    assert "Matti Virtanen" not in flagged_names(conn)
    assert "Matti Virtanen" not in suspect_names(conn)


def test_a_name_repeating_the_venue_is_rejected(conn):
    add_contact(conn, 2, email="b@bierstube.fi", contact_name="Bierstube Helsinki",
                contact_role=None, source="website_other", source_url="u",
                confidence="low")
    assert "Bierstube Helsinki" in flagged_names(conn)


def test_clearing_a_name_never_removes_the_email(conn):
    add_contact(conn, 1, email="info@aoi.fi", contact_name="Facebook Instagram",
                contact_role=None, source="website_other", source_url="u",
                confidence="low")
    rows = gather(conn)
    rejects, _suspects, _sites = analyse(rows, 3, group_owners(conn))
    for r in rows:
        if r["id"] in rejects:
            conn.execute("UPDATE contacts SET contact_name=NULL,"
                         " contact_role=NULL WHERE id=?", (r["id"],))
    conn.commit()
    kept = conn.execute("SELECT email, contact_name FROM contacts").fetchone()
    assert kept["email"] == "info@aoi.fi" and kept["contact_name"] is None
