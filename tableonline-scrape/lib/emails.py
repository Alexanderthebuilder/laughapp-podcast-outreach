"""Email, person and social extraction from restaurant websites (Phase 4d).

Four harvesting channels, in descending confidence:
  1. mailto: hrefs
  2. Cloudflare-obfuscated addresses (data-cfemail hex, XOR with first byte)
  3. "name [at] domain [dot] fi" style obfuscation
  4. plain regex over visible text

Everything then passes a rejection list — asset filenames and CMS/agency
vendor domains are the bulk of the false positives on small restaurant sites.
"""
from __future__ import annotations

import os
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .normalise import strip_diacritics

# The address our own User-Agent advertises, so it is never collected.
CONTACT = os.environ.get("CRAWL_CONTACT_EMAIL", "alex@letsumai.com")

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b")

# "etunimi (at) ravintola [dot] fi", "info ät domain piste fi".
# Whitespace is allowed only around a genuinely obfuscated separator. A bare
# "@" or "." must be unspaced, otherwise the pattern spans the gap between two
# elements and invents an address out of two unrelated fragments.
_AT = r"(?:\s*[\[({]\s*(?:@|at|\u00e4t)\s*[\])}]\s*|\s+(?:at|\u00e4t|miuku|at-merkki)\s+|@)"
_DOT = r"(?:\s*[\[({]\s*(?:\.|dot|piste|punkt)\s*[\])}]\s*|\s+(?:dot|piste|punkt)\s+|\.)"
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)" + _AT + r"([A-Za-z0-9.\-]+)" + _DOT + r"([A-Za-z]{2,24})\b",
    re.IGNORECASE)

# Domains that belong to the CMS, the agency, or the analytics vendor — never
# to the restaurant. A single sentry.io address poisons an outreach list.
VENDOR_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "email.com",
    "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com",
    "sentry.wixpress.com", "squarespace.com", "wordpress.com", "wordpress.org",
    "automattic.com", "webflow.com", "shopify.com", "godaddy.com",
    "cloudflare.com", "google.com", "googlemail.com", "gstatic.com",
    "schema.org", "w3.org", "facebook.com", "instagram.com", "youtube.com",
    "adobe.com", "typekit.com", "fontawesome.com", "jquery.com",
    "yourdomain.com", "yourcompany.com", "site.com", "test.com",
    "mailchimp.com", "list-manage.com", "cookiebot.com", "onetrust.com",
    "smartlook.com", "hotjar.com", "matomo.org",
}

# Local parts that are placeholders or asset artefacts.
REJECT_LOCAL = {"email", "your", "youremail", "name", "user", "username",
                "someone", "noreply", "no-reply", "donotreply", "example"}

ASSET_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|woff2?|ttf|eot|mp4|pdf)$",
                      re.IGNORECASE)
# "logo@2x.png" is the classic false positive.
RETINA_RE = re.compile(r"@[23]x\b", re.IGNORECASE)

# Roles worth storing, per Phase 4d. Order matters only for readability.
ROLE_KEYWORDS = {
    # Finnish
    "ravintolapäällikkö": "restaurant manager",
    "ravintolapaallikko": "restaurant manager",
    "vuoropäällikkö": "shift manager",
    "keittiöpäällikkö": "head chef",
    "keittiopaallikko": "head chef",
    "myyntipäällikkö": "sales manager",
    "myyntipaallikko": "sales manager",
    "toimitusjohtaja": "managing director",
    "omistaja": "owner",
    "yrittäjä": "entrepreneur/owner",
    "yrittaja": "entrepreneur/owner",
    "hovimestari": "maitre d'",
    "keittiömestari": "executive chef",
    "keittiomestari": "executive chef",
    "tapahtumakoordinaattori": "events coordinator",
    "myyntipalvelu": "sales desk",
    # Estonian
    "juhataja": "manager",
    "tegevjuht": "CEO",
    "omanik": "owner",
    "peakokk": "head chef",
    "juhatuse liige": "board member",
    "restoranijuht": "restaurant manager",
    "müügijuht": "sales manager",
    "muugijuht": "sales manager",
    # English
    "restaurant manager": "restaurant manager",
    "general manager": "general manager",
    "head chef": "head chef",
    "owner": "owner",
    "managing director": "managing director",
    "sales manager": "sales manager",
    "events manager": "events manager",
    "event manager": "events manager",
    "ceo": "CEO",
    "founder": "founder",
    "data controller": "data controller",
    "rekisterinpitäjä": "data controller",
    "rekisterinpitaja": "data controller",
    "vastuuhenkilö": "responsible person",
    "vastutav isik": "responsible person",
    "andmekaitsespetsialist": "data protection officer",
    "tietosuojavastaava": "data protection officer",
}

# A person name: two-plus capitalised words, allowing Nordic/Baltic letters.
NAME_RE = re.compile(
    r"\b([A-ZÄÖÅÕÜŠŽ][a-zäöåõüšž]{1,20}(?:-[A-ZÄÖÅÕÜŠŽ][a-zäöåõüšž]{1,20})?"
    r"(?:\s+[A-ZÄÖÅÕÜŠŽ][a-zäöåõüšž]{1,20}){1,2})\b")

# A capitalised run on a policy page is usually prose, not a person:
# "Estonia This Privacy" reads as a name to a pattern that only checks
# capitalisation. A candidate containing ANY of these is rejected outright.
# Deliberately excludes words that are also real Finnish/Estonian surnames
# (Salo, Nurmi, Koski, Tamm, Kask), which is why place names stop at the
# handful that nobody is called.
NON_NAME_TOKENS = {
    # English function words
    "this", "that", "these", "those", "the", "and", "or", "but", "for",
    "with", "your", "our", "their", "its", "his", "her", "all", "any",
    "each", "such", "more", "most", "read", "see", "here", "there", "please",
    "we", "us", "you", "they", "is", "are", "was", "were", "be", "been",
    "will", "can", "may", "must", "shall", "should", "would", "when",
    "what", "which", "who", "whom", "how", "why", "from", "into", "about",
    "also", "not", "only", "than", "then", "have", "has", "had", "does",
    "do", "did", "if", "as", "at", "by", "on", "in", "of", "to",
    # Policy, legal and site furniture
    "privacy", "policy", "policies", "cookie", "cookies", "terms",
    "conditions", "condition", "data", "protection", "notice", "statement",
    "rights", "right", "use", "usage", "using", "service", "services",
    "information", "personal", "processing", "controller", "processor",
    "consent", "gdpr", "law", "legal", "purpose", "purposes", "period",
    "storage", "security", "website", "site", "page", "content", "account",
    "subject", "party", "parties", "third", "general", "regulation",
    "tietosuoja", "tietosuojaseloste", "rekisteriseloste", "evasteet",
    "kayttoehdot", "privaatsuspoliitika", "privaatsus", "andmekaitse",
    "kupsised", "isikuandmete", "tootlemine",
    # Countries and the few city names nobody is surnamed after
    "finland", "estonia", "suomi", "eesti", "sweden", "ruotsi",
    "helsinki", "tallinn", "tartu", "espoo", "vantaa", "tampere",
    # Business furniture
    "company", "ltd", "limited", "group", "team", "menu", "booking",
    "bookings", "reservation", "reservations", "table", "online", "gift",
    "card", "hotel", "opening", "hours", "address", "email", "phone",
    "welcome", "home", "news", "blog", "shop", "order", "delivery",
    # Social platforms and share widgets — these turned up as "names" in a
    # live run, because a capitalised link label looks exactly like a surname.
    "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok",
    "whatsapp", "pinterest", "snapchat", "telegram", "messenger", "share",
    "follow", "like", "subscribe", "newsletter", "tripadvisor", "yelp",
    "wolt", "foodora", "resmio", "quandoo", "tableonline", "opentable",
    # Site furniture and commerce words
    "giftcard", "giftcards", "lahjakortti", "lahjakortit", "kinkekaart",
    "kinkekaardid", "voucher", "vouchers", "takeaway", "catering",
    "lunch", "dinner", "brunch", "breakfast", "buffet", "lounas",
    "gallery", "photos", "images", "video", "press", "calendar", "map",
    "directions", "parking", "allergens", "allergy", "vegan", "vegetarian",
    "gluten", "wine", "beer", "cocktail", "cocktails", "drinks", "bar",
    "kitchen", "chef", "staff", "search", "login", "register", "account",
    "basket", "cart", "checkout", "back", "next", "previous", "close",
    "open", "closed", "today", "tomorrow", "week", "weekend", "season",
    "summer", "winter", "spring", "autumn", "christmas", "joulu", "uusi",
    "vuosi", "year", "happy", "hour", "sitemap", "language", "english",
    "suomi", "svenska", "eesti",
}

# Words that look like names by shape but are not people.
NOT_A_NAME = {
    "privacy policy", "cookie policy", "data controller", "restaurant manager",
    "head chef", "general manager", "opening hours", "contact us", "about us",
    "group bookings", "private events", "gift card", "table online",
    "tietosuojaseloste", "ota yhteytta", "ravintola oy", "helsinki finland",
    "tallinn estonia", "all rights", "read more", "book table",
}

# Role accounts. A person's name near one of these is a coincidence of layout,
# not the mailbox owner, so we never attribute a person to them. Phase 7 puts
# these on the Organization instead of inventing a Person.
GENERIC_LOCALS = {
    "info", "contact", "kontakt", "yhteystiedot", "myynti", "sales",
    "booking", "bookings", "varaus", "varaukset", "reservations", "reserve",
    "hello", "hei", "tere", "office", "toimisto", "posti", "mail", "email",
    "admin", "asiakaspalvelu", "customerservice", "support", "tilaukset",
    "orders", "catering", "events", "tapahtumat", "uritused", "peod",
    "rekry", "recruitment", "jobs", "tyopaikat", "hr", "laskutus",
    "invoice", "invoices", "arve", "billing", "shop", "kauppa", "press",
    "media", "marketing", "markkinointi", "tietosuoja", "privacy", "gdpr",
}

ATTRIBUTION_WINDOW = 200  # characters either side of the address


def _flat(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def decode_cfemail(hexstr: str) -> str | None:
    """Decode a Cloudflare data-cfemail value.

    The first byte is the XOR key; every subsequent byte is the plaintext
    character XORed with it.
    """
    try:
        data = bytes.fromhex(hexstr.strip())
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    try:
        out = "".join(chr(b ^ key) for b in data[1:])
    except ValueError:
        return None
    return out if EMAIL_RE.fullmatch(out) else None


def is_plausible_email(addr: str) -> bool:
    addr = (addr or "").strip().strip(".,;:!?)(<>\"'").lower()
    if not addr or not EMAIL_RE.fullmatch(addr):
        return False
    # Our own crawler address. Politeness requires putting a contact address in
    # the User-Agent, and some sites echo that header back into the page — so
    # it gets harvested as a lead, and appeared twice in a live run.
    if addr == (CONTACT or "").strip().lower():
        return False
    if RETINA_RE.search(addr) or ASSET_RE.search(addr):
        return False
    local, _, domain = addr.partition("@")
    if local in REJECT_LOCAL or local.startswith("u0") or len(local) > 64:
        return False
    if domain in VENDOR_DOMAINS:
        return False
    # Subdomain of a vendor: sentry-next.wixpress.com, cdn.shopify.com
    if any(domain == v or domain.endswith("." + v) for v in VENDOR_DOMAINS):
        return False
    if domain.count(".") > 4 or ".." in addr:
        return False
    return True


def clean_email(addr: str) -> str:
    return addr.strip().strip(".,;:!?)(<>\"'").lower()


def extract_emails(html: str, text: str | None = None) -> list[dict]:
    """All defensible addresses in a page, with channel and confidence.

    Returns dicts of {email, channel, confidence, offset} where offset is the
    position in `text` used for person attribution (None when unknown).
    """
    html = _flat(html)
    text = _flat(text if text is not None else html)
    found: dict[str, dict] = {}

    def add(addr: str, channel: str, confidence: str, offset: int | None):
        addr = clean_email(addr)
        if not is_plausible_email(addr):
            return
        prev = found.get(addr)
        rank = {"high": 3, "medium": 2, "low": 1}
        if prev is None or rank[confidence] > rank[prev["confidence"]]:
            found[addr] = {"email": addr, "channel": channel,
                           "confidence": confidence,
                           "offset": offset if offset is not None else
                                     (prev or {}).get("offset")}

    # 1. mailto: — highest confidence, the site author meant this to be clicked
    for m in re.finditer(r'mailto:([^"\'>\s?]+)', html, re.IGNORECASE):
        addr = unquote(m.group(1))
        add(addr, "mailto", "high", text.find(clean_email(addr)))

    # 2. Cloudflare obfuscation
    for m in re.finditer(r'data-cfemail=["\']([0-9a-fA-F]+)["\']', html):
        decoded = decode_cfemail(m.group(1))
        if decoded:
            add(decoded, "cfemail", "high", None)

    # 3. [at]/[dot] obfuscation — deliberate, so the address is real.
    #    The pattern also matches plain addresses; those belong to channel 4.
    for m in OBFUSCATED_RE.finditer(text):
        if EMAIL_RE.fullmatch(m.group(0).strip()):
            continue
        add(f"{m.group(1)}@{m.group(2)}.{m.group(3)}", "obfuscated", "high",
            m.start())

    # 4. plain text
    for m in EMAIL_RE.finditer(text):
        add(m.group(0), "text", "medium", m.start())

    return list(found.values())


# Capitalised label words that NAME_RE happily swallows into a name.
NAME_STOPWORDS = {
    "yhteyshenkilo", "yhteystiedot", "rekisterinpitaja", "tietosuojaseloste",
    "kontaktisik", "kontakt", "contact", "person", "email", "e", "mail",
    "puh", "tel", "phone", "ravintola", "restaurant", "restoran", "oy", "ab",
    "ou", "as", "hei", "moi", "tere", "toimipiste", "osoite", "aadress",
    "vastutav", "isik", "andmekaitse", "juhatuse", "liige", "vastaava",
}


# Every individual word appearing in a role title. A role sits directly beside
# the name it belongs to ("Myyntipaallikko Liisa Koskinen"), and NAME_RE has no
# way to tell a capitalised job title from a capitalised forename.
_ROLE_WORDS = {w for kw in ROLE_KEYWORDS
               for w in strip_diacritics(kw).lower().split()}


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _mask_labels(window: str) -> str:
    """Blank out label and role words, preserving character offsets.

    NAME_RE matches a run of capitalised words, and a capitalised job title
    directly in front of a name is part of that run ("Head Chef Anna Nurmi"
    captures three words and loses the surname). Masking the title in place
    breaks the run without shifting any offset used for distance ranking.
    """
    def repl(m: re.Match) -> str:
        token = strip_diacritics(m.group(0)).lower()
        if token in NAME_STOPWORDS or token in _ROLE_WORDS:
            return "\u00b7" * len(m.group(0))
        return m.group(0)

    return _WORD_RE.sub(repl, window)


def _trim_labels(name: str) -> str:
    """Drop leading/trailing label and role words.

    "Yhteyshenkilo Matti Virtanen" and "Myyntipaallikko Liisa Koskinen" both
    reduce to the two-word human name.
    """
    def _is_label(token: str) -> bool:
        t = strip_diacritics(token).lower().strip(".,:;")
        return t in NAME_STOPWORDS or t in _ROLE_WORDS

    parts = name.split()
    while parts and _is_label(parts[0]):
        parts.pop(0)
    while parts and _is_label(parts[-1]):
        parts.pop()
    return " ".join(parts)


def is_generic_mailbox(email: str | None) -> bool:
    """True for info@/myynti@/booking@ style shared inboxes (Phase 7)."""
    if not email or "@" not in email:
        return False
    local = email.split("@")[0].lower()
    return local in GENERIC_LOCALS or re.sub(r"[._\-].*", "", local) in GENERIC_LOCALS


def _looks_like_person(name: str) -> bool:
    flat = strip_diacritics(name).lower().strip()
    if flat in {strip_diacritics(n) for n in NOT_A_NAME}:
        return False
    if any(kw in flat for kw in ("policy", "oy", "ltd", "restaurant", "ravintola")):
        return False
    parts = name.split()
    if not (2 <= len(parts) <= 3 and all(len(p) >= 2 for p in parts)):
        return False
    # One prose word is enough to disqualify the whole candidate.
    return not any(strip_diacritics(p).lower().strip(".,:;") in NON_NAME_TOKENS
                   for p in parts)


def attribute_person(text: str, email: str, offset: int | None = None
                     ) -> tuple[str | None, str | None]:
    """Scan +/-200 characters around an address for a name and a role.

    This is what turns a generic harvest into the >=40% named-contact target:
    on a GDPR privacy page the data controller is frequently the owner, named,
    with a direct address.
    """
    text = _flat(text)
    if offset is None or offset < 0:
        offset = text.lower().find(email.lower())
    if offset < 0:
        return None, None
    lo = max(0, offset - ATTRIBUTION_WINDOW)
    hi = min(len(text), offset + len(email) + ATTRIBUTION_WINDOW)
    window = text[lo:hi]
    flat = strip_diacritics(window).lower()

    # The role nearest the address wins. A privacy page names the data
    # controller at the top and the manager beside their own address; picking
    # the first match in the window would label every address "data controller".
    anchor = offset - lo
    role, role_dist = None, None
    for kw, label in ROLE_KEYWORDS.items():
        k = strip_diacritics(kw).lower()
        for m in re.finditer(re.escape(k), flat):
            dist = abs(m.start() - anchor)
            if role_dist is None or dist < role_dist:
                role, role_dist = label, dist

    local = email.split("@")[0].lower()
    if local in GENERIC_LOCALS or re.sub(r"[._\-].*", "", local) in GENERIC_LOCALS:
        return None, role

    # Prefer the name closest to the address.
    masked = _mask_labels(window)
    best, best_dist = None, None
    for m in NAME_RE.finditer(masked):
        cand = _trim_labels(m.group(1).strip())
        if not _looks_like_person(cand):
            continue
        dist = abs(m.start() - (offset - lo))
        # A name whose parts appear in the local part is almost certainly the
        # owner of the address ("mari.tamm@" next to "Mari Tamm").
        if any(strip_diacritics(p).lower() in strip_diacritics(local).lower()
               for p in cand.split()):
            dist -= 150
        if best_dist is None or dist < best_dist:
            best, best_dist = cand, dist
    return best, role


SOCIAL_PATTERNS = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?"),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.\-]+)/?"),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/([A-Za-z0-9_.\-]+)/?"),
}
SOCIAL_JUNK = {"sharer", "share", "tr", "plugins", "dialog", "profile.php",
               "pages", "people", "hashtag", "explore", "p", "reel", "sharer.php"}


def extract_socials(html: str) -> list[dict]:
    out: dict[tuple[str, str], dict] = {}
    for platform, pat in SOCIAL_PATTERNS.items():
        for m in pat.finditer(html or ""):
            handle = m.group(1)
            if handle.lower() in SOCIAL_JUNK or len(handle) < 2:
                continue
            url = m.group(0).rstrip("/")
            out[(platform, url)] = {"platform": platform, "handle": handle,
                                    "url": url}
    return list(out.values())
