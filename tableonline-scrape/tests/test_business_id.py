"""The registry join key. A wrong ID joins to a stranger's company."""
import pytest

from lib.business_id import (extract_business_ids, find_kmkr, find_registrikood,
                             find_ytunnus, normalise_ytunnus,
                             validate_registrikood, validate_ytunnus,
                             ytunnus_check_digit)


@pytest.mark.parametrize("value", ["3632327-4", "0109862-8", "1234567-1"])
def test_valid_ytunnus_accepted(value):
    assert validate_ytunnus(value)


@pytest.mark.parametrize("value", ["2345678-9", "1234567-2", "123456-7",
                                   "12345678", "", None, "abcdefg-1"])
def test_invalid_ytunnus_rejected(value):
    assert not validate_ytunnus(value)


def test_remainder_one_has_no_check_digit():
    """Remainder 1 means no valid check digit exists for that body."""
    bodies = [f"{n:07d}" for n in range(1000)]
    assert any(ytunnus_check_digit(b) is None for b in bodies)


def test_phone_numbers_and_dates_are_not_collected():
    text = "Puh. 09-1234567, avoinna 2015-10-11. Y-tunnus: 3632327-4"
    assert find_ytunnus(text) == ["3632327-4"]


def test_leading_zero_is_restored():
    assert normalise_ytunnus("109862-8") == "0109862-8"
    assert normalise_ytunnus("FI36323274") == "3632327-4"
    assert normalise_ytunnus("nonsense") is None


def test_registrikood_requires_a_keyword_nearby():
    assert find_registrikood("Registrikood: 12345678") == [("12345678", "registrikood")]
    assert find_registrikood("Helista meile 12345678 kohe") == []


def test_registrikood_keyword_must_be_within_the_window():
    far = "registrikood" + " x" * 60 + " 12345678"
    assert find_registrikood(far) == []


def test_kmkr_captured():
    assert find_kmkr("KMKR nr EE101234567") == ["EE101234567"]


def test_registrikood_shape():
    assert validate_registrikood("12345678")
    assert not validate_registrikood("92345678")   # implausible leading digit
    assert not validate_registrikood("1234567")


def test_extract_orders_by_country_hint():
    text = "Y-tunnus 3632327-4 ja registrikood 12345678"
    assert [r["country"] for r in extract_business_ids(text, "EE")][0] == "EE"
    assert [r["country"] for r in extract_business_ids(text, "FI")][0] == "FI"
