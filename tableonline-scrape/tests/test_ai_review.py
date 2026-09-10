"""The model pass is a second filter over the deterministic one."""
import pytest

from lib.db import add_contact, connect, init_db
from src.ai_review_names import SCHEMA, apply_verdicts, candidates


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
    assert apply_verdicts(conn) == 1
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
    assert apply_verdicts(conn) == 0


def test_schema_forbids_extra_fields():
    """Structured output bounds what the model can return, which is also what
    keeps scraped strings from steering the response."""
    assert SCHEMA["additionalProperties"] is False
    item = SCHEMA["properties"]["verdicts"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"index", "is_person", "confidence", "reason"}
