"""The model pass is a second filter over the deterministic one."""
import pytest

from lib.db import add_contact, connect, init_db
from src.ai_review_names import (SCHEMA, apply_verdicts, candidates,
                                 fill_names_from_emails)


@pytest.fixture()
def conn():
    c = connect(":memory:")
    init_db(c)
    for tid, name in ((1, "Ravintola Aoi"), (2, "Bierstube")):
        c.execute("INSERT INTO restaurants (tableonline_id, name) VALUES (?,?)",
                  (tid, name))
    for tid, nm, em in [(1, "Matti Virtanen", "matti@aoi.fi"),
                        (1, "Karjalan Piirakka", "info@aoi.fi"),
                        (2, "Pekka Salo", "pekka@b.fi")]:
        add_contact(c, tid, email=em, contact_name=nm, contact_role=None,
                    source="website_privacy", source_url="p", confidence="high")
    return c


def test_every_unjudged_name_is_a_candidate(conn):
    assert {c["name"] for c in candidates(conn, recheck=False)} == {
        "Matti Virtanen", "Karjalan Piirakka", "Pekka Salo"}


def test_a_cached_verdict_is_not_re_judged(conn):
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person)"
                 " VALUES ('matti virtanen','Matti Virtanen',1)")
    names = {c["name"] for c in candidates(conn, recheck=False)}
    assert "Matti Virtanen" not in names
    assert "Pekka Salo" in names


def test_recheck_re_judges_everything(conn):
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person)"
                 " VALUES ('matti virtanen','Matti Virtanen',1)")
    assert len(candidates(conn, recheck=True)) == 3


def test_applying_clears_only_the_rejected_name(conn):
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person)"
                 " VALUES ('karjalan piirakka','Karjalan Piirakka',0)")
    cleared, rewritten = apply_verdicts(conn)
    assert (cleared, rewritten) == (1, 0)
    rows = {r["email"]: r["contact_name"] for r in
            conn.execute("SELECT email, contact_name FROM contacts")}
    assert rows["info@aoi.fi"] is None
    assert rows["matti@aoi.fi"] == "Matti Virtanen"


def test_applying_never_removes_an_email(conn):
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person)"
                 " VALUES ('karjalan piirakka','Karjalan Piirakka',0)")
    apply_verdicts(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0] == 3


def test_no_verdicts_is_a_no_op(conn):
    assert apply_verdicts(conn) == (0, 0)


def test_a_polluted_name_is_rewritten_not_deleted(conn):
    """"Marja Falenius Sahkoposti" is a real contact with the Finnish word for
    email stuck to it. Deleting it loses a person; trimming it keeps one."""
    add_contact(conn, 2, email="marja@b.fi",
                contact_name="Marja Falenius Sähköposti", contact_role=None,
                source="website_contact", source_url="c", confidence="high")
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person,"
                 " cleaned_name) VALUES (?,?,1,?)",
                 ("marja falenius sahkoposti", "Marja Falenius Sähköposti",
                  "Marja Falenius"))
    cleared, rewritten = apply_verdicts(conn)
    assert (cleared, rewritten) == (0, 1)
    got = conn.execute("SELECT contact_name FROM contacts WHERE email=?",
                       ("marja@b.fi",)).fetchone()[0]
    assert got == "Marja Falenius"


def test_a_name_needing_no_cleaning_is_left_alone(conn):
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person,"
                 " cleaned_name) VALUES ('matti virtanen','Matti Virtanen',1,"
                 " 'Matti Virtanen')")
    assert apply_verdicts(conn) == (0, 0)


def test_a_name_with_no_verdict_is_untouched(conn):
    """Only judged names change; anything unseen is left for the next pass."""
    assert apply_verdicts(conn) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM contacts"
                        " WHERE contact_name IS NOT NULL").fetchone()[0] == 3


def test_schema_forbids_extra_fields():
    """Structured output bounds what the model can return, which is also what
    keeps scraped strings from steering the response."""
    assert SCHEMA["additionalProperties"] is False
    item = SCHEMA["properties"]["verdicts"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"index", "is_person", "cleaned_name",
                                     "confidence", "reason"}


def test_a_cleared_row_can_be_refilled_from_its_address(conn):
    """The model clears junk; the address then supplies the real name."""
    add_contact(conn, 2, email="petri.sahlsten@delicatessen.fi",
                contact_name="Aukioloajat Ma", contact_role=None,
                source="website_other", source_url="u", confidence="low")
    conn.execute("INSERT INTO name_verdicts (name_norm, name, is_person)"
                 " VALUES ('aukioloajat ma','Aukioloajat Ma',0)")
    apply_verdicts(conn)
    assert fill_names_from_emails(conn) == 1
    got = conn.execute("SELECT contact_name FROM contacts WHERE email=?",
                       ("petri.sahlsten@delicatessen.fi",)).fetchone()[0]
    assert got == "Petri Sahlsten"


def test_filling_never_overwrites_a_name_the_model_kept(conn):
    conn.execute("UPDATE contacts SET email='matti.virtanen@aoi.fi'"
                 " WHERE contact_name='Matti Virtanen'")
    conn.commit()
    assert fill_names_from_emails(conn) == 0
    assert conn.execute("SELECT contact_name FROM contacts WHERE email=?",
                        ("matti.virtanen@aoi.fi",)).fetchone()[0] == "Matti Virtanen"


def test_a_shared_inbox_is_never_given_a_name(conn):
    conn.execute("UPDATE contacts SET contact_name=NULL WHERE email='info@aoi.fi'")
    conn.commit()
    fill_names_from_emails(conn)
    assert conn.execute("SELECT contact_name FROM contacts WHERE email=?",
                        ("info@aoi.fi",)).fetchone()[0] is None
