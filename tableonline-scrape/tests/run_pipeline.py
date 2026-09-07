"""End-to-end offline pipeline run. Usage: python tests/run_pipeline.py <db>"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_web import FakeClient, PLACES  # noqa: E402
from lib.db import connect, init_db  # noqa: E402


def run(db: str, registry_dir: Path) -> None:
    import src.phase1_enumerate as p1
    import src.phase2_detail as p2
    import src.phase3_places as p3
    import src.phase4_website_crawl as p4
    import src.phase5_registry as p5
    import src.phase6_secondary as p6
    import src.phase7_pipedrive as p7

    for mod in (p1, p2, p3, p4, p5, p6):
        mod.PoliteClient = FakeClient
    p7.PoliteClient = FakeClient

    # Playwright drives a real browser and does not go through PoliteClient,
    # so tier 3 would reach the public internet and pull live pages into the
    # fixtures. Stubbing it keeps the suite hermetic; where a site would have
    # been rendered, the crawl falls through to the static tier as it does for
    # any site the browser cannot help with.
    p4.tier3 = lambda *a, **kw: 0

    p3.search_text = lambda client, key, query, country: (
        200, next((v for k, v in PLACES.items()
                   if _name_for(k).lower() in query.lower()), {"places": []}))
    os.environ["GOOGLE_PLACES_API_KEY"] = "test"

    common = ["--db", db, "--no-report"]
    print("\n=== PHASE 1 sweep ===")
    p1.main(common + ["sweep", "--start", "1", "--end", "1600"])

    print("\n=== PHASE 2 http ===")
    p2.main(common + ["http"])

    print("\n=== PHASE 3 match + seed websites ===")
    p3.main(common + ["match"])
    p3.main(common + ["seed-websites"])

    print("\n=== PHASE 4 crawl ===")
    p4.main(common + ["crawl"])
    _assert_offline(db)

    print("\n=== PHASE 5 registry ===")
    p5.main(common + ["fi-load", "--file", str(registry_dir / "fi.json")])
    p5.main(common + ["ee-load",
                      "--companies", str(registry_dir / "ee_companies.csv"),
                      "--board", str(registry_dir / "ee_board.csv")])
    p5.main(common + ["match"])
    p5.main(common + ["groups"])

    print("\n=== PHASE 6 patterns ===")
    p6.main(common + ["patterns"])

    print("\n=== PHASE 7 score + push (dry run) + export ===")
    p7.main(common + ["score"])
    p7.main(["--db", db, "push", "--dry-run"])
    p7.main(["--db", db, "export"])


def _assert_offline(db: str) -> None:
    """Guard the guard: if a site was rendered, the run was not hermetic."""
    import sqlite3
    conn = sqlite3.connect(db)
    rendered = conn.execute(
        "SELECT COUNT(*) FROM websites WHERE tier_used='tier3_render'").fetchone()[0]
    conn.close()
    if rendered:
        raise AssertionError(
            f"{rendered} site(s) went through tier 3 — the offline run reached "
            "the real internet")


_NAMES = {1528: "Restaurant Aoi", 92: "Elevant", 1204: "Bona Fide",
          1495: "Vegan Restoran V"}


def _name_for(tid: int) -> str:
    return _NAMES[tid]


def write_registry_fixtures(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)

    def company(bid, name, tol, street, no, post, names=None, extra=None):
        rec = {"businessId": {"value": bid},
               "names": [{"name": n, "type": "1" if i == 0 else "2",
                          "endDate": None}
                         for i, n in enumerate(names or [name])],
               "mainBusinessLine": {"type": tol, "descriptions": [
                   {"languageCode": "1", "description": "Ravintolat"}]},
               "addresses": [{"type": 1, "street": street, "buildingNumber": no,
                              "postCode": post, "postOffices": [
                                  {"city": "HELSINKI", "languageCode": "1"}]}],
               "registeredEntries": [{"type": "41", "register": "7", "status": "1"},
                                     {"type": "80", "register": "6", "status": "1"}],
               "companySituations": [], "tradeRegisterStatus": "1"}
        if extra:
            rec.update(extra)
        return rec

    (d / "fi.json").write_text(json.dumps([
        company("3632327-4", "Ravintola Aoi Oy", "56101", "Kalevankatu", "3",
                "00100", names=["Ravintola Aoi Oy", "Aoi"]),
        company("0109862-8", "Bona Fide Oy", "56101", "Iso Roobertinkatu", "12",
                "00120"),
    ]), encoding="utf-8")

    (d / "ee_companies.csv").write_text(
        "ariregistri_kood;nimi;ettevotja_staatus_tekstina;"
        "asukoht_ettevotja_aadressis;indeks;asukoha_ehak_tekstina\n"
        "12345678;Elevant OÜ;Registrisse kantud;Vana turg 1;10140;Tallinn\n",
        encoding="utf-8")
    (d / "ee_board.csv").write_text(
        "ariregistri_kood;eesnimi;perenimi;isiku_roll\n"
        "12345678;Mari;Tamm;juhatuse liige\n"
        "12345678;Jaan;Kask;juhatuse esimees\n", encoding="utf-8")
    return d


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else tempfile.mktemp(suffix=".sqlite")
    reg = write_registry_fixtures(Path(tempfile.mkdtemp()) / "reg")
    conn = connect(db)
    init_db(conn)
    conn.close()
    run(db, reg)
    print(f"\nDB: {db}")
