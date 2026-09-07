"""Phase 1a parsing guards."""
from lib.tableonline import extract_restaurant_links, parse_listing, probe_url


def meta(**kw):
    return "".join(
        f'<meta property="og:{k}" content="{v}">' for k, v in kw.items())


def test_meta_parsing_is_attribute_order_independent():
    html = ('<meta content="Aoi | TableOnline.fi" property="og:title">'
            '<link href="https://www.tableonline.fi/en/helsinki/aoi/1528" rel=canonical>')
    parsed = parse_listing(html, 1528)
    assert parsed["ok"] and parsed["name"] == "Aoi"
    assert parsed["city_slug"] == "helsinki"


def test_meta_values_do_not_leak_across_tags():
    html = (meta(title="Aoi | TableOnline.fi", description="Nice place.")
            + '<meta content="https://img.tableonline.fi/restaurant/1528/h.jpg"'
              ' property="og:image">')
    parsed = parse_listing(html, 1528)
    assert parsed["description"] == "Nice place."
    assert parsed["image_id"] == 1528


def test_empty_og_title_is_refused():
    """Record 635 returns " | TableOnline.fi" — a deprecated/merged listing."""
    html = (meta(title=" | TableOnline.fi",
                 image="https://img.tableonline.fi/restaurant/309/h.jpg")
            + '<link rel="canonical" href="https://www.tableonline.fi/en/helsinki/k/635">')
    parsed = parse_listing(html, 635)
    assert not parsed["ok"] and parsed["reason"] == "empty_og_title"


def test_image_id_mismatch_flags_needs_review():
    html = (meta(title="Kitchen | TableOnline.fi",
                 image="https://img.tableonline.fi/restaurant/309/h.jpg")
            + '<link rel="canonical" href="https://www.tableonline.fi/en/helsinki/k/635">')
    parsed = parse_listing(html, 635)
    assert parsed["ok"] and parsed["needs_review"] == 1
    assert "309" in parsed["review_reason"]


def test_html_entities_are_decoded():
    html = meta(title="Kitchen &amp; Table | TableOnline.fi") + \
        '<link rel="canonical" href="https://www.tableonline.fi/en/helsinki/kt/635">'
    assert parse_listing(html, 635)["name"] == "Kitchen & Table"


def test_city_page_links_include_book_variant():
    html = ('<a href="/en/tallinn/elevant/92">E</a>'
            '<a href="/en/tallinn/roof/1132/book">R</a>'
            '<a href="/en/about">x</a>')
    assert extract_restaurant_links(html) == [(92, "/en/tallinn/elevant/92"),
                                              (1132, "/en/tallinn/roof/1132")]


def test_probe_url_is_slug_independent():
    assert probe_url(92).endswith("/en/x/x/92")
