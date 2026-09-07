"""Filesystem layout. Everything resolves off the project root so phases can be
run from any working directory."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
RAW = ROOT / "raw"
RAW_PAGES = RAW / "pages"
RAW_WEBSITES = RAW / "websites"
RAW_REGISTRY = RAW / "registry"
RAW_PLACES = RAW / "places"
EXPORTS = ROOT / "exports"
DOCS = ROOT / "docs"
RUN_REPORT = ROOT / "run_report.md"

DB_PATH = Path(os.environ.get("TABLEONLINE_DB", DB_DIR / "tableonline.sqlite"))


def ensure_dirs() -> None:
    for p in (DB_DIR, RAW, RAW_PAGES, RAW_WEBSITES, RAW_REGISTRY, RAW_PLACES,
              EXPORTS, DOCS):
        p.mkdir(parents=True, exist_ok=True)
