from lib.normalise import (addresses_agree, haversine_m, name_similarity,
                           normalise_name, parse_street, strip_diacritics)


def test_brand_resolves_to_registered_trade_name():
    """The mechanism that gets "Aoi" to "Ravintola Aoi Oy" (Phase 5 key 2)."""
    assert normalise_name("Ravintola Aoi Oy") == normalise_name("Aoi") == "aoi"
    assert name_similarity("Aoi", "Ravintola Aoi Oy") == 1.0


def test_wholly_generic_name_keeps_its_descriptive_word():
    assert normalise_name("Kohvik OÜ") == normalise_name("Kohvik") == "kohvik"


def test_unrelated_names_score_below_the_accept_threshold():
    assert name_similarity("Elevant", "Bona Fide Oy") < 0.8


def test_nordic_and_baltic_folding():
    assert strip_diacritics("Pärnu Õlle Süda Šašlõkk") == "Parnu Olle Suda Saslokk"


def test_street_split_and_abbreviation():
    assert parse_street("Mannerheimintie 12 A, 00100 Helsinki") == ("mannerheimintie", "12")
    assert parse_street("Tartu mnt 5")[0] == "tartu maantee"


def test_addresses_need_matching_postcode():
    assert addresses_agree("kalevankatu", "3", "00100", "kalevankatu", "3", "00100")
    assert not addresses_agree("kalevankatu", "3", "00100", "kalevankatu", "3", "00120")


def test_haversine_is_metres():
    assert 110 < haversine_m(60.1699, 24.9384, 60.1709, 24.9384) < 113
