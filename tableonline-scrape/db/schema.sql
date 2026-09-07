-- TableOnline Attack List — schema
-- Principle 1: no in-memory state. Every phase writes here and is re-runnable.
-- Principle 2: idempotent by natural key.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Phase 1 / 2 — the restaurant spine. Natural key: tableonline_id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS restaurants (
    tableonline_id      INTEGER PRIMARY KEY,
    name                TEXT,
    restaurant_slug     TEXT,
    city_slug           TEXT,
    city                TEXT,
    country             TEXT,              -- 'FI' | 'EE' | NULL when slug unmapped
    street_address      TEXT,
    postal_code         TEXT,
    phone               TEXT,
    description         TEXT,              -- og:description, reused for outreach
    cuisine_tags        TEXT,              -- JSON array
    atmosphere_tags     TEXT,              -- JSON array
    review_score        REAL,
    review_count        INTEGER,
    is_michelin         INTEGER DEFAULT 0,
    has_active_offer    INTEGER DEFAULT 0,
    accepts_giftcard    INTEGER DEFAULT 0,
    tableonline_url     TEXT,
    og_image            TEXT,
    image_id            INTEGER,           -- id parsed out of img.../restaurant/{id}/
    tenure_bucket       INTEGER,           -- 1 = oldest quartile (lowest ids)
    discovery_source    TEXT,              -- id_enum | city_page | id_enum+city_page
    needs_review        INTEGER DEFAULT 0,
    review_reason       TEXT,
    phase1_at           TEXT,
    phase2_status       TEXT,              -- pending | http_ok | rendered | failed
    phase2_at           TEXT,
    scraped_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_rest_country ON restaurants(country);
CREATE INDEX IF NOT EXISTS ix_rest_p2 ON restaurants(phase2_status);

-- Every ID probed, including the 404s. The gaps are churn intelligence.
CREATE TABLE IF NOT EXISTS enumeration_log (
    tableonline_id      INTEGER PRIMARY KEY,
    http_status         INTEGER,
    canonical           TEXT,
    og_title            TEXT,
    checked_at          TEXT,
    note                TEXT
);

-- Phase 1a-bis: ground truth from rendered city listing pages.
CREATE TABLE IF NOT EXISTS city_page_listings (
    tableonline_id      INTEGER,
    city_slug           TEXT,
    url                 TEXT,
    found_at            TEXT,
    PRIMARY KEY (tableonline_id, city_slug)
);

CREATE TABLE IF NOT EXISTS city_slugs (
    city_slug           TEXT PRIMARY KEY,
    city                TEXT,
    country             TEXT,
    source              TEXT,              -- city_selector | fallback_map | unmapped
    first_seen          TEXT
);

-- ---------------------------------------------------------------------------
-- Phase 3 — Google Places (New).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS places (
    restaurant_id       INTEGER PRIMARY KEY REFERENCES restaurants(tableonline_id),
    place_id            TEXT,
    display_name        TEXT,
    formatted_address   TEXT,
    website_uri         TEXT,
    international_phone TEXT,
    business_status     TEXT,              -- OPERATIONAL | CLOSED_PERMANENTLY | ...
    rating              REAL,
    user_rating_count   INTEGER,
    lat                 REAL,
    lng                 REAL,
    primary_type        TEXT,
    name_similarity     REAL,
    distance_m          REAL,
    match_method        TEXT,              -- name | distance | name+distance | none
    accepted            INTEGER DEFAULT 0,
    needs_review        INTEGER DEFAULT 0,
    query_used          TEXT,
    raw_path            TEXT,
    matched_at          TEXT
);

-- ---------------------------------------------------------------------------
-- Phase 4 — website crawl.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS websites (
    restaurant_id       INTEGER PRIMARY KEY REFERENCES restaurants(tableonline_id),
    domain              TEXT,
    base_url            TEXT,
    cms                 TEXT,              -- wordpress | squarespace | wix | webflow | unknown
    tier_used           TEXT,              -- tier1_cms | tier2_static | tier3_render | firecrawl
    pages_fetched       INTEGER DEFAULT 0,
    status              TEXT,              -- pending | ok | blocked | dead | failed
    error               TEXT,
    crawled_at          TEXT
);

CREATE TABLE IF NOT EXISTS website_pages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id       INTEGER REFERENCES restaurants(tableonline_id),
    url                 TEXT,
    page_kind           TEXT,              -- privacy | events | careers | contact | about | home | other
    priority            INTEGER,
    http_status         INTEGER,
    raw_path            TEXT,
    fetched_at          TEXT,
    UNIQUE (restaurant_id, url)
);

-- Principle 3: every contact row records source, source_url and confidence.
-- dedupe_key is the natural key within a restaurant: the email when we have
-- one, else name:<normalised name> so registry board members dedupe too.
CREATE TABLE IF NOT EXISTS contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id       INTEGER REFERENCES restaurants(tableonline_id),
    email               TEXT,
    contact_name        TEXT,
    contact_role        TEXT,
    source              TEXT NOT NULL,     -- website_privacy | website_events | website_contact
                                           -- | website_careers | website_other | registry_ee
                                           -- | places | jobad | whois | pattern_guess
    source_url          TEXT,
    confidence          TEXT NOT NULL,     -- verified | high | medium | low | catchall_guess | unverified
    verification_status TEXT,              -- deliverable | undeliverable | accept_all | unknown
    verified_at         TEXT,
    found_at            TEXT,
    dedupe_key          TEXT NOT NULL,
    UNIQUE (restaurant_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_contacts_rest ON contacts(restaurant_id);
CREATE INDEX IF NOT EXISTS ix_contacts_source ON contacts(source);

CREATE TABLE IF NOT EXISTS business_ids (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id       INTEGER REFERENCES restaurants(tableonline_id),
    business_id         TEXT NOT NULL,
    country             TEXT NOT NULL,     -- FI (y-tunnus) | EE (registrikood)
    vat_id              TEXT,              -- EE KMKR / FI VAT where seen
    source_url          TEXT,
    confidence          TEXT,              -- checksum_valid | keyword_corroborated | weak
    method              TEXT,              -- website_footer | website_privacy | registry | ...
    found_at            TEXT,
    UNIQUE (restaurant_id, business_id)
);

CREATE TABLE IF NOT EXISTS socials (
    restaurant_id       INTEGER REFERENCES restaurants(tableonline_id),
    platform            TEXT,              -- instagram | facebook | linkedin | other
    handle              TEXT,
    url                 TEXT,
    source_url          TEXT,
    found_at            TEXT,
    PRIMARY KEY (restaurant_id, platform, url)
);

-- ---------------------------------------------------------------------------
-- Phase 5 — business registers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies_fi (
    business_id         TEXT PRIMARY KEY,  -- businessId.value
    primary_name        TEXT,
    names_json          TEXT,              -- full names[] array with types
    main_business_line  TEXT,              -- mainBusinessLine.type (TOL 2008)
    main_business_desc  TEXT,
    company_forms       TEXT,              -- JSON
    addresses_json      TEXT,              -- JSON, postCode casing preserved
    street              TEXT,
    building_number     TEXT,
    post_code           TEXT,
    post_office         TEXT,
    website             TEXT,              -- schema says so; measure fill rate
    registration_date   TEXT,
    trade_register_status TEXT,
    status              TEXT,
    end_date            TEXT,
    last_modified       TEXT,
    in_employer_register INTEGER DEFAULT 0,-- registeredEntries type 41 register 7
    vat_liable          INTEGER DEFAULT 0, -- registeredEntries type 80 register 6
    situations_json     TEXT,              -- companySituations; empty array = clean
    situation_flags     TEXT,              -- liquidation | bankruptcy | restructuring
    name_norm           TEXT,              -- normalised primary name, for fallback join
    names_norm          TEXT               -- newline-joined normalised names[]
);
CREATE INDEX IF NOT EXISTS ix_fi_namenorm ON companies_fi(name_norm);
CREATE INDEX IF NOT EXISTS ix_fi_postcode ON companies_fi(post_code);
CREATE INDEX IF NOT EXISTS ix_fi_tol ON companies_fi(main_business_line);

CREATE TABLE IF NOT EXISTS companies_ee (
    registrikood        TEXT PRIMARY KEY,
    name                TEXT,
    legal_form          TEXT,
    status              TEXT,
    address             TEXT,
    street              TEXT,
    post_code           TEXT,
    city                TEXT,
    ehak_code           TEXT,
    vat_id              TEXT,
    emtaks              TEXT,              -- EMTAK activity code where present
    name_norm           TEXT,
    raw_json            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ee_namenorm ON companies_ee(name_norm);

CREATE TABLE IF NOT EXISTS company_board_ee (
    registrikood        TEXT,
    person_name         TEXT,
    role                TEXT,              -- juhatuse liige etc.
    PRIMARY KEY (registrikood, person_name, role)
);

CREATE TABLE IF NOT EXISTS registry_matches (
    restaurant_id       INTEGER PRIMARY KEY REFERENCES restaurants(tableonline_id),
    country             TEXT,
    business_id         TEXT,
    company_name        TEXT,
    match_method        TEXT,              -- business_id | name | name+address | website | none
    match_confidence    REAL,
    tol_code            TEXT,
    tol_label           TEXT,
    status              TEXT,
    in_employer_register INTEGER,
    vat_liable          INTEGER,
    situation_flags     TEXT,
    excluded_reason     TEXT,              -- liquidation | bankruptcy | ceased | ...
    needs_review        INTEGER DEFAULT 0,
    matched_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_regmatch_bid ON registry_matches(business_id);

-- 5c. One company operating four venues is one enterprise conversation.
CREATE TABLE IF NOT EXISTS groups (
    business_id         TEXT PRIMARY KEY,
    group_parent        TEXT,              -- registered company name
    venue_count         INTEGER,
    country             TEXT,
    detected_at         TEXT
);

-- ---------------------------------------------------------------------------
-- Phase 6 / 7.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_ads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id       INTEGER REFERENCES restaurants(tableonline_id),
    board               TEXT,              -- duunitori | oikotie | cv.ee
    url                 TEXT,
    posted_date         TEXT,
    contact_name        TEXT,
    contact_email       TEXT,
    contact_phone       TEXT,
    found_at            TEXT,
    UNIQUE (restaurant_id, url)
);

CREATE TABLE IF NOT EXISTS scores (
    restaurant_id       INTEGER PRIMARY KEY REFERENCES restaurants(tableonline_id),
    priority_score      REAL,
    components          TEXT,              -- JSON breakdown, so weights can be retuned
    track               TEXT,              -- smb | enterprise (groups)
    scored_at           TEXT
);

CREATE TABLE IF NOT EXISTS pipedrive_sync (
    restaurant_id       INTEGER PRIMARY KEY REFERENCES restaurants(tableonline_id),
    org_id              INTEGER,
    person_ids          TEXT,              -- JSON map email -> person id
    payload_hash        TEXT,              -- skip no-op updates on re-run
    synced_at           TEXT
);

CREATE TABLE IF NOT EXISTS run_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phase               TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    ok                  INTEGER,
    counts              TEXT,              -- JSON
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS failures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phase               TEXT,
    restaurant_id       INTEGER,
    target              TEXT,
    error               TEXT,
    occurred_at         TEXT
);
