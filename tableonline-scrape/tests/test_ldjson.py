"""Picking the right JSON-LD node out of a dozen on one page."""
import json

from lib.detail_parse import parse_detail
from lib.ldjson import extract, find_restaurant


def script(obj):
    return ('<script type="application/ld+json">'
            + json.dumps(obj) + "</script>")


STUB = {"@type": "Restaurant", "@id": "/en/helsinki/plein/1000"}
NEIGHBOUR = {"@type": "Restaurant", "name": "Neighbour",
             "@id": "/en/helsinki/other/995",
             "address": {"streetAddress": "Muu 1"}, "telephone": "+358 1",
             "geo": {"latitude": 60.1, "longitude": 24.9},
             "servesCuisine": "Nordic", "description": "x"}
SUBJECT = {"@type": "Restaurant", "name": "Plein",
           "@id": "/en/helsinki/plein/1000",
           "telephone": "+358 40 123 4567",
           "address": {"@type": "PostalAddress", "streetAddress": "Sturenkatu 27",
                       "postalCode": "00510", "addressLocality": "Helsinki"},
           "aggregateRating": {"ratingValue": "4.7", "reviewCount": "210"}}

PAGE = script(STUB) + script(NEIGHBOUR) + script(SUBJECT)


def test_page_id_selects_the_pages_own_subject():
    """A stub appears first and a richer neighbour second; only the @id match
    reaches the right one."""
    assert find_restaurant(PAGE, 1000)["name"] == "Plein"


def test_without_a_page_id_the_richest_node_wins():
    assert find_restaurant(PAGE)["name"] == "Neighbour"


def test_an_empty_stub_never_wins_on_position_alone():
    assert find_restaurant(script(STUB) + script(SUBJECT), 1000)["name"] == "Plein"


def test_carousel_of_other_restaurants_is_not_mistaken_for_the_subject():
    carousel = "".join(
        script({"@type": "Restaurant", "name": f"Other {i}",
                "@id": f"/en/kuopio/x/{990 + i}"}) for i in range(6))
    assert find_restaurant(carousel + script(SUBJECT), 1000)["name"] == "Plein"


def test_fields_extracted_from_the_chosen_node():
    got = extract(PAGE, 1000)
    assert got["street_address"] == "Sturenkatu 27"
    assert got["postal_code"] == "00510"
    assert got["phone"] == "+358 40 123 4567"
    assert got["review_score"] == 4.7 and got["review_count"] == 210


def test_parse_detail_threads_the_page_id_through():
    got = parse_detail(PAGE, 1000)
    assert got["street_address"] == "Sturenkatu 27"
    assert got["phone"] == "+358 40 123 4567"
    # Without it, the neighbour's address would be attributed to this venue.
    assert parse_detail(PAGE)["street_address"] == "Muu 1"


def test_breadcrumb_listitems_are_ignored():
    crumbs = script({"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "url": "/en/helsinki"}]})
    assert find_restaurant(crumbs + script(SUBJECT), 1000)["name"] == "Plein"
