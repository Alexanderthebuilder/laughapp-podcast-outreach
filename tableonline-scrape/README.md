# TableOnline Attack List

A contact-enriched lead database of every restaurant using TableOnline in
Finland and Estonia, joined to the national business registers and loaded into
Pipedrive as the "TableOnline Attack List".

Target: ~600–1,100 restaurants, ≥85% with a deliverable email, ≥40% with a
named human contact.

## Where this runs

**On the VPS (72.62.76.233), inside a named tmux session.** The full sweep plus
website crawls is several hours of wall time and must survive disconnection.
The host also needs outbound access to `tableonline.fi`, `avoindata.prh.fi`,
`avaandmed.ariregister.rik.ee`, `places.googleapis.com` and `api.pipedrive.com`.

```bash
tmux new -s tableonline
cd tableonline-scrape
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # Phases 1a-bis, 2 render, 4 tier 3
cp .env.example .env               # then fill it in — .env is gitignored
```

## Credentials you must supply

| Needed for | What | When |
|---|---|---|
| Phase 3 | Google Places API key. GCP project with billing enabled and **"Places API (New)"** enabled specifically — not the legacy Places API. Restrict the key to that API. | Before Phase 3 — the one hard blocker |
| Phase 7 | Pipedrive API token (personal settings → API) | Before Phase 7 |
| Phase 6 | Email verifier account — MillionVerifier or Bouncer | Deferrable |
| Phase 4 Tier 3 | Firecrawl key | Only if Cloudflare-blocked domains appear; skip initially |

All of these live in `.env`, which is gitignored. No key is ever inlined in
source.

## Run order

Every phase takes `--limit` and `--resume`. **Prove each one with `--limit 20`
before running it full-scale.** Review `run_report.md` between phases.

```bash
# Phase 1 — enumeration
python -m src.phase1_enumerate discover                    # robots, sitemap, city selector, bundle APIs
python -m src.phase1_enumerate crosscheck                  # 1a-bis: rule out silent under-collection FIRST
python -m src.phase1_enumerate sweep --limit 20            # prove it
python -m src.phase1_enumerate sweep --resume              # IDs 1..2500
python -m src.phase1_enumerate coverage --sample 50        # 1c: confirm the gaps are real 404s

# Phase 2 — detail
python -m src.phase2_detail http --resume                  # meta tags, no browser, uses the stored raw corpus
python -m src.phase2_detail render --limit 20              # then --resume for the rest

# Phase 3 — Google Places  (needs GOOGLE_PLACES_API_KEY)
python -m src.phase3_places match --limit 20
python -m src.phase3_places match --resume
python -m src.phase3_places seed-websites

# Phase 4 — website crawl
python -m src.phase4_website_crawl crawl --limit 20
python -m src.phase4_website_crawl crawl --resume

# Phase 5 — business registers
python -m src.phase5_registry fi-download
python -m src.phase5_registry fi-load
python -m src.phase5_registry ee-load \
    --companies raw/registry/ettevotja_rekvisiidid.csv.zip \
    --board     raw/registry/kandevalised.csv.zip
python -m src.phase5_registry match
python -m src.phase5_registry groups

# Phase 6 — secondary sources
python -m src.phase6_secondary patterns
python -m src.phase6_secondary verify                      # needs EMAIL_VERIFIER + key
python -m src.phase6_secondary whois
python -m src.phase6_secondary jobads --limit 20

# Phase 7 — scoring and Pipedrive
python -m src.phase7_pipedrive score
python -m src.phase7_pipedrive export                      # CSV, review before it hits the CRM
python -m src.phase7_pipedrive setup                       # resolve/create custom fields
python -m src.phase7_pipedrive push --dry-run              # payloads to exports/, nothing sent
python -m src.phase7_pipedrive push
```

The Estonian bulk files must be downloaded by hand from
<https://avaandmed.ariregister.rik.ee> (downloading open-data section) — the
published filenames change between releases, so `ee-load` takes explicit paths
and resolves columns by matching header names rather than positions. If it
loads zero rows it says so instead of silently succeeding.

## Design principles

1. **No in-memory state.** Every phase writes to the database as it goes and is
   independently re-runnable. A crashed run resumes; it does not restart.
2. **Idempotent by natural key.** `tableonline_id` for restaurants,
   `(restaurant_id, email)` for contacts.
3. **Every contact records `source`, `source_url` and `confidence`** — we need
   to know which channel produced booked meetings.
4. **Polite crawling.** One request in flight per host, 1–2 s apart, a real
   User-Agent carrying a contact address, exponential backoff on 429/503. This
   runs overnight; speed is irrelevant and a block is expensive.
5. **Cheap before expensive.** No paid API is called for a field a free source
   already filled.
6. **Raw responses are never overwritten.** Everything lands in `raw/` first
   and parsing is a separate step, so improving an extractor costs no crawl:
   `phase2_detail reparse` and `phase4_website_crawl reparse` re-harvest the
   stored corpus with no network at all.

## Layout

```
tableonline-scrape/
├── docs/discovery.md       # confirmed facts + autogenerated discovery findings
├── db/schema.sql
├── src/phase1..7           # one CLI per phase, each with --limit and --resume
├── lib/                    # business_id, normalise, emails, pagekind, registries, http, db
├── raw/                    # every raw response, never overwritten (gitignored)
├── exports/                # CSV and Pipedrive payloads (gitignored)
├── tests/                  # offline suite, including a full pipeline run
└── run_report.md           # regenerated after every phase (gitignored)
```

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The suite runs entirely offline: `tests/fake_web.py` stands in for the network,
and `tests/test_pipeline.py` drives all seven phases end to end and then repeats
the whole run over the same database to prove it produces no duplicates.

## Reading run_report.md

Regenerated from the database after every phase, so it is accurate after a
partial or resumed run. It carries counts per phase, contact yield per source,
the failure list, and a table scoring the acceptance criteria against their
targets.
