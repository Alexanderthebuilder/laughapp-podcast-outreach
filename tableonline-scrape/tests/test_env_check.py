"""A masked secret must be refused, not written."""
import pytest

from src import env_check
from src.env_check import (
    ANTHROPIC_KEY_RE, GOOGLE_KEY_RE, describe_bad, non_ascii)

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


ANTHROPIC_REAL = "sk-ant-api03-" + "aB3" * 30


def test_a_real_anthropic_key_matches():
    assert ANTHROPIC_KEY_RE.match(ANTHROPIC_REAL)


@pytest.mark.parametrize("bad", [
    "sk-ant-",                       # prefix with nothing after it
    "sk-ant-short",                  # truncated paste
    "sk-ant-api03-" + "•" * 40,      # copied from a display that masks it
    "AIzaSyA_dTjxZvgoMYhngEVfuMM5H2GaZjVP1eI",   # the wrong key entirely
    " sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ",  # leading space
])
def test_malformed_anthropic_values_rejected(bad):
    assert not ANTHROPIC_KEY_RE.match(bad)


def test_appending_to_a_file_with_no_trailing_newline(tmp_path, monkeypatch):
    """The bug this replaced: `echo K=v >> .env` glues the new assignment onto
    whatever the last line was, and both keys are then unreadable."""
    env = tmp_path / ".env"
    env.write_text("GOOGLE_PLACES_API_KEY=" + REAL)   # no trailing newline
    monkeypatch.setattr(env_check, "ENV", env)

    env_check.set_value(f"ANTHROPIC_API_KEY={ANTHROPIC_REAL}")

    lines = env.read_text().splitlines()
    assert lines == [f"GOOGLE_PLACES_API_KEY={REAL}",
                     f"ANTHROPIC_API_KEY={ANTHROPIC_REAL}"]
    assert env.read_text().endswith("\n")


def test_setting_a_key_twice_leaves_one_assignment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY=sk-ant-old{'x' * 30}\n")
    monkeypatch.setattr(env_check, "ENV", env)

    env_check.set_value(f"ANTHROPIC_API_KEY={ANTHROPIC_REAL}")

    assert env.read_text() == f"ANTHROPIC_API_KEY={ANTHROPIC_REAL}\n"
    assert env_check.duplicates() == []


def test_a_masked_anthropic_key_is_refused_not_written(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GOOGLE_PLACES_API_KEY=" + REAL + "\n")
    monkeypatch.setattr(env_check, "ENV", env)

    with pytest.raises(SystemExit):
        env_check.set_value("ANTHROPIC_API_KEY=sk-ant-" + "•" * 40)

    assert "ANTHROPIC" not in env.read_text()
