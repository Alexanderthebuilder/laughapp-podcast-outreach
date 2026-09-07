"""A stand-in for the network, so the whole pipeline can be exercised offline.

Every phase talks to the outside world through lib.http.PoliteClient, so
swapping that one class out gives a deterministic end-to-end run.
"""
from __future__ import annotations

import json

from lib.http import Response


def listing(name, city, slug, tid, image_id=None):
    image_id = tid if image_id is None else image_id
    return (f'<meta property="og:title" content="{name} | TableOnline.fi">'
            f'<meta property="og:description" content="{name} — a place to eat.">'
            f'<meta property="og:image" content="https://img.tableonline.fi/restaurant/{image_id}/h.jpg">'
            f'<link rel="canonical" href="https://www.tableonline.fi/en/{city}/{slug}/{tid}">')


TABLEONLINE = {
    92: listing("Elevant", "tallinn", "elevant", 92),
    635: listing(" ", "helsinki", "kitchen-and-table-helsinki", 635, image_id=309),
    1204: listing("Bona Fide", "helsinki", "bona-fide", 1204),
    1495: listing("Vegan Restoran V", "tallinn", "vegan-restoran-v", 1495),
    1528: listing("Restaurant Aoi", "helsinki", "restaurant-aoi", 1528),
}

WP_PAGES = json.dumps([{
    "link": "https://ravintola-aoi.fi/tietosuojaseloste",
    "title": {"rendered": "Tietosuojaseloste"},
    "content": {"rendered":
        "<p>Rekisterinpit&auml;j&auml;: Ravintola Aoi Oy, Y-tunnus 3632327-4, "
        "Kalevankatu 3, 00100 Helsinki. Yhteyshenkil&ouml; Matti Virtanen, "
        "omistaja, <a href='mailto:matti.virtanen@ravintola-aoi.fi'>"
        "matti.virtanen@ravintola-aoi.fi</a>.</p>"}}])

SITES = {
    "https://ravintola-aoi.fi": (
        '<html><head><link href="/wp-content/t.css" rel="stylesheet"></head>'
        '<body><p>Ravintola Aoi, Helsinki.</p>'
        '<a href="mailto:info@ravintola-aoi.fi">info</a>'
        '<a href="https://instagram.com/ravintolaaoi">IG</a></body></html>', 200),
    "https://ravintola-aoi.fi/wp-json/wp/v2/pages?per_page=100&page=1&_fields=id,link,title,content": (WP_PAGES, 200),
    "https://ravintola-aoi.fi/wp-json/wp/v2/posts?per_page=50&page=1&_fields=id,link,title,content": ("[]", 200),
    "https://ravintola-aoi.fi/wp-json/wp/v2/users?per_page=100&_fields=id,name,slug": ("[]", 200),

    "https://elevant.ee": (
        '<html><body><p>Restoran Elevant, Tallinn</p>'
        '<a href="/privaatsuspoliitika">Privaatsuspoliitika</a>'
        '<a href="/kontakt">Kontakt</a></body></html>', 200),
    "https://elevant.ee/privaatsuspoliitika": (
        '<html><body><p>Vastutav t&ouml;&ouml;tleja: Elevant O&Uuml;, '
        'registrikood 12345678, KMKR EE101234567, Vana turg 1, 10140 Tallinn.'
        '</p></body></html>', 200),
    "https://elevant.ee/kontakt": (
        '<html><body><a href="mailto:info@elevant.ee">info</a></body></html>', 200),

    "https://bonafide.fi": (
        '<html><body><p>Bona Fide Helsinki</p>'
        '<a href="/yhteystiedot">Yhteystiedot</a></body></html>', 200),
    "https://bonafide.fi/yhteystiedot": (
        '<html><body><p>Myyntip&auml;&auml;llikk&ouml; Liisa Koskinen, '
        '<a href="mailto:liisa.koskinen@bonafide.fi">liisa.koskinen@bonafide.fi</a>'
        '</p></body></html>', 200),
}

PLACES = {
    1528: {"places": [{"id": "p1", "displayName": {"text": "Ravintola Aoi"},
        "formattedAddress": "Kalevankatu 3, 00100 Helsinki",
        "websiteUri": "https://ravintola-aoi.fi", "businessStatus": "OPERATIONAL",
        "internationalPhoneNumber": "+358 9 6128 5100",
        "rating": 4.6, "userRatingCount": 312, "primaryType": "restaurant",
        "location": {"latitude": 60.1675, "longitude": 24.9385}}]},
    92: {"places": [{"id": "p2", "displayName": {"text": "Elevant"},
        "formattedAddress": "Vana turg 1, 10140 Tallinn",
        "websiteUri": "https://elevant.ee", "businessStatus": "OPERATIONAL",
        "internationalPhoneNumber": "+372 631 3132",
        "rating": 4.4, "userRatingCount": 980, "primaryType": "restaurant",
        "location": {"latitude": 59.4372, "longitude": 24.7453}}]},
    1204: {"places": [{"id": "p3", "displayName": {"text": "Bona Fide"},
        "formattedAddress": "Iso Roobertinkatu 12, 00120 Helsinki",
        "websiteUri": "https://bonafide.fi", "businessStatus": "OPERATIONAL",
        "internationalPhoneNumber": "+358 44 123 4567",
        "rating": 4.2, "userRatingCount": 140, "primaryType": "restaurant",
        "location": {"latitude": 60.1620, "longitude": 24.9430}}]},
    1495: {"places": [{"id": "p4", "displayName": {"text": "V Vegan Restoran"},
        "formattedAddress": "Rataskaevu 12, 10123 Tallinn",
        "businessStatus": "CLOSED_PERMANENTLY", "rating": 4.7,
        "userRatingCount": 500, "primaryType": "restaurant",
        "location": {"latitude": 59.4380, "longitude": 24.7440}}]},
}


class FakeClient:
    """Serves the fixtures above; anything unknown 404s."""

    def __init__(self, *a, **k):
        pass

    def get(self, url, **kw):
        if "tableonline.fi/en/x/x/" in url:
            tid = int(url.rstrip("/").split("/")[-1])
            if tid in TABLEONLINE:
                return Response(url, 200, TABLEONLINE[tid], {})
            return Response(url, 404, "", {})
        if url in SITES:
            body, status = SITES[url]
            return Response(url, status, body, {})
        return Response(url, 404, "", {})

    def request(self, method, url, **kw):
        return self.get(url, **kw)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass
