"""Anchor text first, slug second — and privacy before contact."""
from lib.pagekind import classify, is_crawlable, rank_links


def test_anchor_text_finds_a_privacy_page_at_an_opaque_url():
    assert classify("Tietosuojaseloste", "https://x.fi/page-17") == ("privacy", 1)
    assert classify("Privaatsuspoliitika", "https://x.ee/?p=45") == ("privacy", 1)


def test_slug_is_the_fallback_when_there_is_no_anchor_text():
    assert classify("", "https://x.fi/tietosuoja") == ("privacy", 1)
    assert classify("", "https://x.ee/kontakt") == ("contact", 5)


def test_privacy_outranks_contact():
    privacy = classify("Tietosuojaseloste", "/a")[1]
    contact = classify("Yhteystiedot", "/b")[1]
    assert privacy < contact


def test_fetch_order_is_privacy_events_careers_home_contact():
    links = [("https://x.fi/menu", "Menu"),
             ("https://x.fi/yhteys", "Yhteystiedot"),
             ("https://x.fi/g", "Ryhmävaraukset"),
             ("https://x.fi/rekry", "Avoimet työpaikat"),
             ("https://x.fi/", ""),
             ("https://x.fi/p17", "Tietosuojaseloste")]
    assert [k for _u, k, _p in rank_links(links, "x.fi")] == \
        ["privacy", "events", "careers", "home", "contact", "other"]


def test_off_host_assets_and_admin_are_not_crawled():
    for url in ["https://instagram.com/x", "https://x.fi/a.pdf",
                "https://x.fi/wp-admin/", "mailto:a@x.fi", "tel:+358"]:
        assert not is_crawlable(url, "x.fi"), url
    assert is_crawlable("https://www.x.fi/about", "x.fi")


def test_duplicate_urls_collapse_keeping_the_best_classification():
    links = [("https://x.fi/p17", "Read more"), ("https://x.fi/p17/", "Tietosuoja")]
    ranked = rank_links(links, "x.fi")
    assert len(ranked) == 1 and ranked[0][1] == "privacy"
