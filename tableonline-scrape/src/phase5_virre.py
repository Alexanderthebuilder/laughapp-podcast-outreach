"""Finnish officers from Virre trade register extracts.

Fetching and parsing are separate commands on purpose. Fetching is slow,
polite and against someone else's server; parsing is instant and will be
re-run every time the parser improves. Keeping them apart means a parser fix
never costs another 439 requests.

  python -m src.phase5_virre fetch --limit 3    # smoke test, 3 companies
  python -m src.phase5_virre fetch              # all of them, resumable
  python -m src.phase5_virre parse              # saved PDFs -> contacts

Both resume: fetch skips a business ID whose PDF is already on disk, parse
skips nothing because it is cheap. A run interrupted at company 300 costs
nothing to continue.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.db import now, upsert
from lib.paths import ROOT
from lib.virre import ROLE_LABEL, parse_extract
from src._cli import base_parser, open_db

PDF_DIR = ROOT / "raw" / "virre"
COMPANY = "https://virre.prh.fi/yritys/{}"

# The extract link, in either interface language.
EXTRACT_LINK = re.compile(r"kaupparekisteriote|trade register extract", re.I)

# One request every few seconds against a public authority's free service.
# There is no rate limit published; this is deliberate restraint, not a
# measured ceiling.
DELAY_S = 3.0


def pending(conn, limit: int | None) -> list[tuple[int, str]]:
    """Finnish business IDs with no extract on disk yet."""
    rows = conn.execute(
        "SELECT DISTINCT b.restaurant_id, b.business_id FROM business_ids b"
        " WHERE b.country='FI' AND b.confidence='checksum_valid'"
        " ORDER BY b.restaurant_id").fetchall()
    todo = [(r["restaurant_id"], r["business_id"]) for r in rows
            if not (PDF_DIR / f"{r['business_id']}.pdf").exists()]
    return todo[:limit] if limit else todo


def fetch(conn, limit: int | None) -> None:
    from playwright.sync_api import sync_playwright

    from lib.consent import dismiss

    todo = pending(conn, limit)
    if not todo:
        print("Every business ID already has an extract on disk.")
        return
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(todo)} extracts to fetch, {DELAY_S:.0f}s apart "
          f"(~{len(todo) * DELAY_S / 60:.0f} min)")

    got = missed = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # One context for the whole run: the site issues a session on first
        # load and reuses it, so a fresh context per company would triple the
        # requests for nothing.
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        for i, (rid, bid) in enumerate(todo, 1):
            body: list[bytes] = []

            def capture(response, _body=body):
                if "application/pdf" in (
                        response.headers.get("content-type") or ""):
                    try:
                        _body.append(response.body())
                    except Exception:
                        pass        # response already consumed by the viewer

            page.on("response", capture)
            try:
                page.goto(COMPANY.format(bid), wait_until="domcontentloaded",
                          timeout=30000)
                dismiss(page)
                link = page.get_by_text(EXTRACT_LINK).first
                link.click(timeout=10000)
                # The PDF arrives as a blob in a new tab, so waiting on a
                # download event never fires. The response listener is what
                # actually catches it.
                page.wait_for_timeout(6000)
            except Exception as exc:
                print(f"  [{i}/{len(todo)}] {bid} failed: "
                      f"{type(exc).__name__}")
            finally:
                page.remove_listener("response", capture)

            if body:
                (PDF_DIR / f"{bid}.pdf").write_bytes(body[-1])
                got += 1
                print(f"  [{i}/{len(todo)}] {bid} saved "
                      f"({len(body[-1]) // 1024} kB)")
            else:
                missed += 1
                print(f"  [{i}/{len(todo)}] {bid} no PDF seen")
            time.sleep(DELAY_S)

        browser.close()
    print(f"\n{got} extracts saved, {missed} missed. "
          f"Re-run to retry the misses.")


def parse(conn, limit: int | None) -> None:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(2)

    by_bid = {r["business_id"]: r["restaurant_id"] for r in conn.execute(
        "SELECT business_id, restaurant_id FROM business_ids"
        " WHERE country='FI'")}

    files = sorted(PDF_DIR.glob("*.pdf"))[:limit or None]
    people = mails = 0
    for path in files:
        rid = by_bid.get(path.stem)
        if rid is None:
            continue
        try:
            text = "\n".join((p.extract_text() or "")
                             for p in PdfReader(str(path)).pages)
        except Exception as exc:
            print(f"  {path.name} unreadable: {type(exc).__name__}")
            continue
        got = parse_extract(text)

        for officer in got["officers"]:
            upsert(conn, "contacts",
                   {"restaurant_id": rid, "dedupe_key": f"virre:{officer.name}"},
                   {"contact_name": officer.name,
                    "contact_role": ROLE_LABEL[officer.role],
                    "source": "registry_fi", "source_url": COMPANY.format(path.stem),
                    # Filed with the register and signed off by the company,
                    # which is a stronger claim than anything scraped.
                    "confidence": "high", "found_at": now()})
            people += 1

        if got.get("email"):
            upsert(conn, "contacts",
                   {"restaurant_id": rid, "dedupe_key": got["email"]},
                   {"email": got["email"], "source": "registry_fi",
                    "source_url": COMPANY.format(path.stem),
                    "confidence": "high", "found_at": now()})
            mails += 1
        conn.commit()

    print(f"{len(files)} extracts read: {people} officers, "
          f"{mails} registered addresses.")
    print("Re-run `python -m src.export_sheet` to rebuild the sheet.")


def main(argv=None) -> None:
    p = base_parser(__doc__)
    p.add_argument("command", choices=["fetch", "parse"])
    args = p.parse_args(argv)
    conn = open_db(args)
    (fetch if args.command == "fetch" else parse)(conn, args.limit)


if __name__ == "__main__":
    main()
