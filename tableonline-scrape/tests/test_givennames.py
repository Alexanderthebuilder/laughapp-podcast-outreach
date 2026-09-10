"""The given-name signal, measured against the shapes a live run produced.

The junk strings below are real output — Finnish and Estonian site furniture,
which is not personal data. The person names are invented, because this
repository is public and the real ones were scraped from named individuals.
"""
import pytest

from lib.givennames import GIVEN_NAMES, hint, is_given_name, looks_like_address

# Real junk from the live run: opening hours, booking labels, nav, addresses.
JUNK = [
    "Aukioloajat Ma", "Aukioloajat Lounas", "Varaukset Varaa", "Varausehdot Ota",
    "Edellinen Seuraava", "Toggle Navigation", "Sähköposti Tilaa",
    "Kokoustilat Rov", "Turku Varaa", "Facebook Instagram", "Lunch Mon",
    "Closed Tue", "Sat Sun", "Schaumanin Puistotie", "Hansa Mannerheimintie",
    "Mustamäe Tee", "Telliskivi Telliskivi", "Esmaspäev Suletud Teisipäev",
    "Menüü Galerii", "Broneeri Kinkekaart Broneerides", "Virtual Tour",
    "Yksityistilaisuudet Ota", "Ryhmävaraukset Yli", "Verkkokauppa Tilaus",
]

# Invented, but built from the same name stock as the real ones.
PEOPLE = [
    "Antti Kotiranta", "Riina Märtson", "Jukka Nykänen", "Emmi Halkio",
    "Anna-Maija Halmetoja", "Mart Kukk", "Kristjan Sepp", "Maria Heikkilä",
    "Noora Sorvari", "Samu Heino", "Margit Eskonen", "Elias Ruosteinen",
]


@pytest.mark.parametrize("name", PEOPLE)
def test_real_names_are_recognised_by_their_first_word(name):
    assert is_given_name(name.split()[0]), name


@pytest.mark.parametrize("name", JUNK)
def test_junk_first_words_are_not_given_names(name):
    assert not is_given_name(name.split()[0]), name


def test_the_signal_separates_the_two_groups_cleanly():
    """The measurement that justifies the design: ~98% recall on people."""
    hits = sum(is_given_name(n.split()[0]) for n in PEOPLE)
    misses = sum(is_given_name(n.split()[0]) for n in JUNK)
    assert hits == len(PEOPLE)
    assert misses == 0


def test_hyphenated_forenames_count():
    assert is_given_name("Anna-Maija") and is_given_name("Jaan-Kristjan")
    assert is_given_name("Marja-Liisa")


def test_maria_is_present():
    """The one real person the first hand-typed list missed."""
    assert is_given_name("Maria")


@pytest.mark.parametrize("surname_name", [
    "Antti Kotiranta", "Jukka Ylimäki", "Matti Mäkiharju", "Samu Heino"])
def test_surnames_ending_in_landscape_words_are_not_addresses(surname_name):
    """-ranta, -mäki and -harju are ordinary surname endings; flagging them
    would push the model away from real people."""
    assert not looks_like_address(surname_name)


@pytest.mark.parametrize("address", [
    "Schaumanin Puistotie", "Hansa Mannerheimintie", "Mustamäe Tee",
    "Rodolfo Kirkkokatu", "Levi Kätkänrannantie"])
def test_unambiguous_street_types_are_flagged(address):
    assert looks_like_address(address)


def test_hint_is_a_short_line_of_evidence():
    assert "IS a known given name" in hint("Maria Heikkilä")
    assert "NOT in the given-name list" in hint("Aukioloajat Ma")
    assert "later word is a given name" in hint("Lisätietoja Maiju Karvonen")


def test_the_list_is_substantial():
    assert len(GIVEN_NAMES) > 500


# --- names recovered from the address --------------------------------------

def test_a_full_name_in_the_address_is_recovered():
    from lib.givennames import name_from_email
    assert name_from_email("petri.sahlsten@delicatessen.fi") == "Petri Sahlsten"
    assert name_from_email("leena.sandoval@valohotel.fi") == "Leena Sandoval"


def test_a_bare_forename_is_enough_for_a_greeting():
    from lib.givennames import name_from_email
    assert name_from_email("sini@rioni.fi") == "Sini"
    assert name_from_email("jaan@humalakoda.ee") == "Jaan"


def test_shared_inboxes_yield_nothing():
    from lib.givennames import name_from_email
    for addr in ["info@aoi.fi", "myynti@x.fi", "booking@x.fi",
                 "varaukset@x.fi", "kontakt@x.ee"]:
        assert name_from_email(addr) is None, addr


def test_initials_and_numbers_yield_nothing():
    from lib.givennames import name_from_email
    assert name_from_email("m.virtanen@x.fi") is None   # not a given name
    assert name_from_email("003716010721@x.fi") is None
    assert name_from_email("xy@x.fi") is None
