"""Consent overlays are the reason an earlier render found nothing."""
from lib.consent import ACCEPT_SELECTORS, BANNER_SELECTORS


def test_cookieyes_comes_first():
    """The banner observed on tableonline.fi is CookieYes, so try it first."""
    assert ACCEPT_SELECTORS[0] == ".cky-btn-accept"


def test_generic_text_match_is_last():
    """A bare "Accept" would happily click an unrelated dialog, so it may only
    run once every specific selector has failed."""
    assert ACCEPT_SELECTORS[-1] == "button:has-text('Accept')"
    specific = ACCEPT_SELECTORS[:ACCEPT_SELECTORS.index("button[aria-label*='Accept' i]")]
    assert all(not s.startswith("button:has-text") for s in specific)


def test_the_major_vendors_are_covered():
    joined = " ".join(ACCEPT_SELECTORS)
    for vendor in ("cky", "onetrust", "Cybot", "cc-allow"):
        assert vendor in joined


def test_finnish_and_estonian_wording_present():
    joined = " ".join(ACCEPT_SELECTORS)
    assert "Hyväksy" in joined and "Nõustu" in joined


def test_banner_containers_listed_for_removal():
    assert ".cky-consent-container" in BANNER_SELECTORS
    assert "#onetrust-consent-sdk" in BANNER_SELECTORS
