"""Harvesting and person attribution (Phase 4d)."""
import re

import pytest

from lib.emails import (attribute_person, decode_cfemail, extract_emails,
                        extract_socials, is_generic_mailbox, is_plausible_email)


def cf(plain, key=0x7b):
    return format(key, "02x") + "".join(format(ord(c) ^ key, "02x") for c in plain)


def test_cloudflare_obfuscation_decodes():
    assert decode_cfemail(cf("omistaja@ravintola.fi")) == "omistaja@ravintola.fi"
    assert decode_cfemail("zz") is None


def test_vendor_and_asset_addresses_are_rejected():
    for bad in ["abc@sentry.io", "x@sentry-next.wixpress.com", "logo@2x.png",
                "a@example.com", "style@cdn.shopify.com", "you@yourdomain.com"]:
        assert not is_plausible_email(bad), bad


def test_real_addresses_accepted():
    for good in ["info@ravintola-aoi.fi", "mari.tamm@kohvik.ee",
                 "matti@sub.domain.fi"]:
        assert is_plausible_email(good), good


def test_obfuscation_does_not_span_unrelated_fragments():
    """A plain "@" with whitespace around it must not glue two elements."""
    text = "matti.virtanen@aoi.fi . IG"
    assert {e["email"] for e in extract_emails(text, text)} == {"matti.virtanen@aoi.fi"}


def test_worded_obfuscation_still_decodes():
    for text, expected in [("myynti [at] aoi [dot] fi", "myynti@aoi.fi"),
                           ("info (at) kohvik.ee", "info@kohvik.ee"),
                           ("tellimus ät resto piste ee", "tellimus@resto.ee")]:
        got = extract_emails(text, text)
        assert got[0]["email"] == expected
        assert got[0]["channel"] == "obfuscated"


def test_mailto_outranks_body_text():
    html = '<a href="mailto:Info@Aoi.fi">Info@Aoi.fi</a>'
    e = extract_emails(html, "Info@Aoi.fi")[0]
    assert e["email"] == "info@aoi.fi" and e["channel"] == "mailto"


def test_shared_inboxes_never_get_a_person():
    text = "Yhteyshenkilö Matti Virtanen, omistaja. info@aoi.fi"
    assert attribute_person(text, "info@aoi.fi")[0] is None
    assert is_generic_mailbox("varaukset@aoi.fi")
    assert not is_generic_mailbox("matti.virtanen@aoi.fi")


def test_nearest_role_wins_not_the_first_in_the_window():
    text = ("Rekisterinpitäjä Ravintola Aoi Oy. Yhteyshenkilö Matti Virtanen, "
            "ravintolapäällikkö, matti.virtanen@aoi.fi")
    assert attribute_person(text, "matti.virtanen@aoi.fi") == \
        ("Matti Virtanen", "restaurant manager")


def test_role_titles_are_not_absorbed_into_the_name():
    for text, email, expected in [
            ("Myyntipäällikkö Liisa Koskinen, liisa@x.fi", "liisa@x.fi", "Liisa Koskinen"),
            ("Head Chef Anna Nurmi anna@x.fi", "anna@x.fi", "Anna Nurmi"),
            ("Juhatuse liige Mari Tamm mari@x.ee", "mari@x.ee", "Mari Tamm")]:
        assert attribute_person(text, email)[0] == expected


def test_social_junk_paths_are_skipped():
    html = ('<a href="https://www.facebook.com/sharer/sharer.php?u=x">s</a>'
            '<a href="https://www.facebook.com/RavintolaAoi">fb</a>'
            '<a href="https://instagram.com/aoihelsinki">ig</a>')
    handles = {s["handle"] for s in extract_socials(html)}
    assert handles == {"RavintolaAoi", "aoihelsinki"}


@pytest.mark.parametrize("addr", [
    "info@mysite.com",          # Wix ships this as the default
    "hello@example.com",
    "contact@yourdomain.com",
    "info@shop.mysite.com",     # and on a subdomain of one
])
def test_template_placeholder_domains_are_rejected(addr):
    """Not a bad address for the restaurant — not the restaurant's address at
    all. It survives on a published site for years because nothing about it
    looks broken, and it will bounce or reach a stranger."""
    assert not is_plausible_email(addr)


@pytest.mark.parametrize("addr", [
    "info@ravintolaperiscope.fi",
    "myynti@nh-hotels.com",       # a hotel group's own domain, not a template
    "info@vapiano.ee",
])
def test_a_real_domain_that_is_not_the_venue_is_still_kept(addr):
    """A group mailing from the parent domain is correct and often the better
    address. Only templates are rejected here, never merely off-domain ones."""
    assert is_plausible_email(addr)


@pytest.mark.parametrize("addr", [
    "alex@letsumai.com",        # the address the crawler announces
    "info@letsumai.com",        # anything else on the same domain
    "hello@mail.letsumai.com",  # and on a subdomain of it
])
def test_our_own_domain_is_never_a_lead(addr):
    """The crawler names a contact address in its User-Agent and some sites
    echo request headers into the page, so it comes back as a harvested lead.
    Matching only the exact address let twelve restaurants end up carrying an
    address on our own domain."""
    assert not is_plausible_email(addr)
