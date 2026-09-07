"""Inspect, validate and repair .env.

preflight.sh used to shell-source .env while the pipeline reads it with
python-dotenv. Two parsers disagree on quoting and continuation, so a file that
looked fine to one could hand the other something else entirely — which is how
a 39-character key became a 101-character value. Everything now goes through
this module, so what is checked is exactly what the run will use.

  python -m src.env_check                 # report on every key
  python -m src.env_check --print GOOGLE_PLACES_API_KEY
  python -m src.env_check --set GOOGLE_PLACES_API_KEY=AIza...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

# A Google API key is "AIza" plus 35 characters from the URL-safe alphabet.
GOOGLE_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")

CHECKS = {
    "GOOGLE_PLACES_API_KEY": (
        GOOGLE_KEY_RE,
        'a Google API key is 39 characters and starts "AIza"'),
}


def load() -> dict:
    """Read .env exactly as the pipeline does."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("python-dotenv is not installed — run: pip install -r requirements.txt",
              file=sys.stderr)
        raise SystemExit(2)
    if not ENV.exists():
        print(f"{ENV} does not exist. Copy .env.example to .env first.",
              file=sys.stderr)
        raise SystemExit(2)
    return {k: v for k, v in dotenv_values(ENV).items() if v is not None}


def _mask(value: str) -> str:
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def duplicates() -> list[str]:
    """Variables assigned more than once — the usual cause of a mangled value."""
    seen: dict[str, int] = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    return [k for k, n in seen.items() if n > 1]


def report() -> int:
    values = load()
    problems = 0

    dupes = duplicates()
    if dupes:
        problems += 1
        print(f"  DUPLICATE assignments in .env: {', '.join(dupes)}")
        print("    The last one wins, and a stray quote makes one swallow the next.")

    for name, (pattern, hint) in CHECKS.items():
        value = (values.get(name) or "").strip()
        if not value:
            print(f"  {name}: not set")
            continue
        if pattern.match(value):
            print(f"  {name}: OK  {_mask(value)}  ({len(value)} chars)")
        else:
            problems += 1
            print(f"  {name}: MALFORMED  {_mask(value)}  ({len(value)} chars)")
            print(f"    {hint}")
            if "\n" in value or " " in value:
                print("    The value contains whitespace or a line break, so the "
                      "assignment is spilling past its own line.")
            print(f"    Repair it with:  python -m src.env_check --set {name}=<value>")
    return problems


def set_value(assignment: str) -> None:
    """Rewrite one variable safely, collapsing any duplicates to a single line."""
    if "=" not in assignment:
        raise SystemExit("expected NAME=value")
    name, _, value = assignment.partition("=")
    name, value = name.strip(), value.strip().strip('"').strip("'")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit(f"{name!r} is not a valid variable name")
    if "\n" in value:
        raise SystemExit("value must be a single line")

    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    out, written = [], False
    for line in lines:
        if re.match(rf"\s*{re.escape(name)}\s*=", line):
            if not written:
                out.append(f"{name}={value}")
                written = True
            # Any further assignment of the same name is dropped, not kept.
            continue
        out.append(line)
    if not written:
        out.append(f"{name}={value}")

    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    ENV.chmod(0o600)
    print(f"{name} set to {_mask(value)} ({len(value)} chars) in {ENV}")

    check = CHECKS.get(name)
    if check and not check[0].match(value):
        print(f"WARNING: {check[1]} — the value written does not match that shape.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", dest="print_var", metavar="NAME",
                   help="print one value, for use by shell scripts")
    p.add_argument("--set", dest="assignment", metavar="NAME=VALUE",
                   help="write one value safely, collapsing duplicates")
    args = p.parse_args(argv)

    if args.assignment:
        set_value(args.assignment)
        return
    if args.print_var:
        sys.stdout.write((load().get(args.print_var) or "").strip())
        return
    raise SystemExit(1 if report() else 0)


if __name__ == "__main__":
    main()
