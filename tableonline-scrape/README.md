# TableOnline Attack List

A contact-enriched lead database of every restaurant using TableOnline in
Finland and Estonia.

The deliverable is a contact sheet — restaurant, contact first name, email,
area, phone — produced by `src/export_sheet.py`. The business-register join
and the Pipedrive load are built and tested, but they are optional extras, not
part of the path to the sheet.

Target: ~600–1,100 restaurants, ≥85% with an email, ≥40% with a named human
contact.

## Setup on the VPS

**On the VPS (72.62.76.233), inside a named tmux session.** The sweep plus the
website crawls run for hours and must survive a dropped connection. The host
needs outbound access to `tableonline.fi` and `places.googleapis.com`.

```bash
tmux new -s tableonline          # or: tmux attach -t tableonline

git clone https://github.com/Alexanderthebuilder/laughapp-podcast-outreach.git
cd laughapp-podcast-outreach/tableonline-scrape

GOOGLE_PLACES_API_KEY=<your-key> ./bootstrap.sh
```

`bootstrap.sh` installs the system packages, builds the virtualenv, installs
Chromium with the shared libraries it needs, writes `.env`, and runs the test
suite. It is idempotent — re-run it any time.

Then confirm the run can actually succeed before spending hours on it:

```bash
. .venv/bin/activate
./preflight.sh
```

It first checks `.env` itself — reading it with python-dotenv, the same parser
the pipeline uses, so what it validates is what the run will actually get. A
duplicated assignment or a stray quote makes one line swallow the next, and the
resulting value is reported with its real length rather than being sent to
Google as-is. Repair any variable with:

```bash
python -m src.env_check --set GOOGLE_PLACES_API_KEY=<key>
```

which rewrites that one line and collapses any duplicates.

It then checks that `tableonline.fi` answers, that the `/en/x/x/{id}` shortcut
returns 200, that the Places key works **from this server's IP**, and that
Chromium launches. If the key is IP-restricted it prints the exact IPv4 to add
in the GCP console. Do not start the run until every line reads OK.

### tmux, briefly

A blank screen with a green bar along the bottom means tmux is running — that
is the expected result of `tmux new`, not an error.

| | |
|---|---|
| Leave it running, close the browser | `Ctrl-b` then `d` |
| Come back later | `tmux attach -t tableonline` |
| Scroll back through output | `Ctrl-b` then `[`, then arrows; `q` to exit |

## Credentials

| Needed for | What | Status |
|---|---|---|
| Phase 3 | Google Places API key, **"Places API (New)"** enabled specifically — not the legacy Places API | In `.env`. The key is IP-restricted, so **add the VPS IP `72.62.76.233`** to it in the GCP console or every call returns `API_KEY_IP_ADDRESS_BLOCKED` |
| Phase 6 verify | MillionVerifier or Bouncer | Optional — only needed to confirm guessed addresses |
| Phase 4 Tier 3 | Firecrawl key | Only if Cloudflare-blocked domains appear |
| Phase 7 | Pipedrive API token | Not needed for the contact sheet |

`.env` is gitignored. No key is ever inlined in source.

## Run order — contact sheet

This is the path to `exports/tableonline_contacts.xlsx`. Phase 7 (Pipedrive) is
not part of it.

Every phase takes `--limit` and `--resume`, on either side of the subcommand.
**Prove each one with `--limit 20` before running it full-scale**, and read
`run_report.md` between phases.

```bash
# Step 1 — is the shortcut safe?  Run this BEFORE the sweep.
python -m src.phase1_enumerate discover
python -m src.phase1_enumerate crosscheck          # prints ZERO MISSES or N MISSES

# Step 2 — find every restaurant  (~40 min: 2500 IDs at 1-2s apart)
python -m src.phase1_enumerate sweep --limit 20    # prove it
python -m src.phase1_enumerate sweep --resume      # the real sweep
python -m src.phase1_enumerate coverage --sample 50

# Step 3 — names and descriptions, then the JS-only fields
python -m src.phase2_detail http --resume
python -m src.phase2_detail render --limit 20
python -m src.phase2_detail render --resume

# Step 4 — Google Places: this is where the phone numbers and websites come from
python -m src.phase3_places match --limit 20
python -m src.phase3_places match --resume
python -m src.phase3_places seed-websites

# Step 5 — the websites: emails and named people  (the long one, run overnight)
python -m src.phase4_website_crawl crawl --limit 20
python -m src.phase4_website_crawl crawl --resume

# Step 6 — the sheet
python -m src.export_sheet
python -m src.export_sheet --only-with-email --name tableonline_sendable
```

### Sheet columns

`Restaurant · First name · Email · Area · Country · Phone`, then supporting
columns: `Full name · Role · Email type · Backup email · Email source ·
Company · TableOnline`.

**Email type** is the column to trust before sending:

| Value | Meaning |
|---|---|
| `personal` | a named person's address, taken from the site |
| `shared inbox` | info@ / myynti@ — real, but nobody is named |
| `guessed` | built from a name and the domain; unverified, may bounce |
| `catch-all (unconfirmed)` | the domain accepts everything, so delivery proves nothing |

`Backup email` carries the best *confirmed* address whenever the primary is a
guess, so a bounce does not lose the lead.

## Optional extras

```bash
# Better first-name coverage in Tallinn and Tartu (see "Estonia" below)
python -m src.phase5_registry ee-load --companies <file> --board <file>
python -m src.phase5_registry match

# Confirm guessed addresses before sending  (needs EMAIL_VERIFIER + key)
python -m src.phase6_secondary patterns
python -m src.phase6_secondary verify

# Finnish registry: company names, payroll signal, excludes bankrupt venues
python -m src.phase5_registry fi-download && python -m src.phase5_registry fi-load
python -m src.phase5_registry match && python -m src.phase5_registry groups

# Pipedrive, if it is ever wanted
python -m src.phase7_pipedrive score
python -m src.phase7_pipedrive push --dry-run
```

## Estonia

Estonian restaurants need no special handling for the sheet. They are on the
same domain as the Finnish ones — the city slug (`tallinn`, `tartu`, `parnu`)
is the only country discriminator — so Steps 1–6 above collect them exactly
like Finland, and `lib/cities.py` maps the slug to `EE`. An unrecognised slug
leaves `country` NULL and is logged as an error rather than defaulted to FI.

The one Estonia-specific gap is **first-name coverage**. Finnish sites are
forced by GDPR to name a data controller on the privacy page, which is where
most Finnish named contacts come from; Estonian sites do this less
consistently. The fix is the e-Business Register, which publishes board
members' names in full (personal ID codes are masked, the names are not):

1. Open <https://avaandmed.ariregister.rik.ee/en/downloading-open-data>.
2. Download the **company details** file (`ettevotja_rekvisiidid`) and the
   **representation / board members** file. Both are free, no key, no account.
3. Drop them in `raw/registry/` and run `ee-load` with the two paths, then
   `match`.

`ee-load` resolves columns by matching header names rather than positions,
because the published filenames and column sets change between releases. If a
file's headers match nothing it prints a warning and loads zero rows rather
than silently succeeding.

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
├── src/export_sheet.py     # the contact sheet (xlsx + csv)
├── bootstrap.sh            # one-time VPS setup, idempotent
├── preflight.sh            # verifies connectivity, API key and browser
├── src/env_check.py        # inspect, validate and repair .env
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
