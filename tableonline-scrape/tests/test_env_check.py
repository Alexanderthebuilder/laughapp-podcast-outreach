"""A masked secret must be refused, not written."""
import pytest

from src.env_check import GOOGLE_KEY_RE, describe_bad, non_ascii

REAL = "AIzaSyA_dTjxZvgoMYhngEVfuMM5H2GaZjVP1eI"
MASKED = "AIzaSyA_" + "•" * 31          # copied from a display that hides it


def test_a_real_google_key_matches():
    assert GOOGLE_KEY_RE.match(REAL) and len(REAL) == 39


def test_a_masked_key_is_the_right_length_but_still_rejected():
    """This is the trap: 39 characters, so a length check alone passes it."""
    assert len(MASKED) == 39
    assert not GOOGLE_KEY_RE.match(MASKED)


def test_masked_characters_are_named():
    assert non_ascii(MASKED) == [("•", 31)]
    notes = " ".join(describe_bad(MASKED))
    assert "U+2022" in notes and "39 characters, 101 bytes" in notes


def test_a_real_key_has_nothing_to_report():
    assert non_ascii(REAL) == []


@pytest.mark.parametrize("bad", ["AIza short", "AIzaSy\nAIzaSy", "not-a-key"])
def test_other_malformed_values_rejected(bad):
    assert not GOOGLE_KEY_RE.match(bad)
