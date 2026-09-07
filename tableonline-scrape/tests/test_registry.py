"""PRH and Estonian register handling."""
from lib.registry_ee import (BOARD_COLUMNS, COMPANY_COLUMNS, is_active,
                             map_headers, normalise_board_member,
                             normalise_company)
from lib.registry_fi import (EXCLUDE_SITUATIONS, business_id_of, keep_record,
                             normalise_record, registered_flags,
                             situation_flags)


def company(**over):
    rec = {
        "businessId": {"value": "3632327-4", "registrationDate": "2015-10-11"},
        "names": [{"name": "Ravintola Aoi Oy", "type": "1", "endDate": None},
                  {"name": "Aoi", "type": "2", "endDate": None}],
        "mainBusinessLine": {"type": "56101", "descriptions": [
            {"languageCode": "1", "description": "Ravintolat"}]},
        "addresses": [
            {"type": 2, "street": "Tilitoimistonkatu", "buildingNumber": "9",
             "postCode": "00500", "postOffices": [{"city": "HELSINKI",
                                                   "languageCode": "1"}]},
            {"type": 1, "street": "Kalevankatu", "buildingNumber": "3",
             "postCode": "00100", "co": "c/o Kirjanpito",
             "postOffices": [{"city": "HELSINKI", "languageCode": "1"}]}],
        "registeredEntries": [{"type": "41", "register": "7", "status": "1"},
                              {"type": "80", "register": "6", "status": "1"}],
        "companySituations": [], "tradeRegisterStatus": "1"}
    rec.update(over)
    return rec


def test_business_id_is_an_object_not_a_string():
    assert business_id_of(company()) == "3632327-4"
    assert business_id_of({"businessId": "1234567-1"}) == "1234567-1"


def test_visiting_address_beats_the_accountants_postal_address():
    n = normalise_record(company())
    assert (n["street"], n["building_number"], n["post_code"]) == \
        ("kalevankatu", "3", "00100")


def test_all_trade_names_are_indexed_so_a_brand_can_join():
    assert "aoi" in normalise_record(company())["names_norm"].split("\n")


def test_employer_and_vat_registers_detected_and_ended_entries_ignored():
    assert registered_flags(company()) == (1, 1)
    ended = company(registeredEntries=[{"type": "41", "register": "7",
                                        "status": "2"}])
    assert registered_flags(ended) == (0, 0)


def test_bankruptcy_excludes_but_restructuring_only_flags():
    bankrupt = company(companySituations=[
        {"type": "KONKURSSI", "descriptions": [
            {"languageCode": "1", "description": "Konkurssi"}]}])
    assert set(situation_flags(bankrupt)) & EXCLUDE_SITUATIONS

    restructuring = company(companySituations=[
        {"type": "SAN", "descriptions": [
            {"languageCode": "1", "description": "Yrityssaneeraus"}]}])
    assert situation_flags(restructuring) == ["restructuring"]
    assert not set(situation_flags(restructuring)) & EXCLUDE_SITUATIONS


def test_keep_record_routes():
    n = normalise_record(company())
    assert keep_record(n, set(), set())                       # target TOL
    off = dict(n, main_business_line="62010", names_norm="")
    assert keep_record(off, {"3632327-4"}, set())             # harvested ID
    assert keep_record(dict(n, main_business_line="62010"), set(), {"aoi"})
    assert not keep_record(dict(off, business_id="1-1"), set(), set())


def test_ee_headers_resolve_by_matching_not_by_position():
    headers = ["ariregistri_kood", "nimi", "ettevotja_staatus_tekstina", "indeks"]
    mapped = map_headers(headers, COMPANY_COLUMNS)
    assert mapped == {0: "registrikood", 1: "name", 2: "status", 3: "post_code"}


def test_ee_company_normalisation_and_status():
    row = {"registrikood": "12345678", "name": "Elevant OÜ",
           "status": "Registrisse kantud", "post_code": "10140"}
    n = normalise_company(row)
    assert n["registrikood"] == "12345678" and n["name_norm"] == "elevant"
    assert is_active("Registrisse kantud")
    assert not is_active("Kustutatud")


def test_board_member_needs_a_two_part_human_name():
    assert normalise_board_member(
        {"registrikood": "12345678", "first_name": "Mari",
         "last_name": "Tamm", "role": "juhatuse liige"})["person_name"] == "Mari Tamm"
    assert normalise_board_member(
        {"registrikood": "12345678", "person_name": "Holding"}) is None


def test_board_columns_map():
    assert map_headers(["ariregistri_kood", "eesnimi", "perenimi", "isiku_roll"],
                       BOARD_COLUMNS) == {0: "registrikood", 1: "first_name",
                                          2: "last_name", 3: "role"}
