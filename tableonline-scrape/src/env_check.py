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


def non_ascii(value: str) -> list[tuple[str, int]]:
    """Distinct non-ASCII characters in a value, with counts.

    A secret pasted from a display that masks it arrives as bullets or
    asterisks. Those are multi-byte, so the value looks the right *length* in
    characters while being nonsense — worth naming explicitly rather than
    leaving the reader to compare a character count against a byte count.
    """
    counts: dict[str, int] = {}
    for ch in value:
        if ord(ch) > 127:
            counts[ch] = counts.get(ch, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def describe_bad(value: str) -> list[str]:
    """Human-readable reasons a value looks wrong."""
    notes = []
    weird = non_ascii(value)
    if weird:
        shown = ", ".join(f"{ch!r} (U+{ord(ch):04X}) x{n}" for ch, n in weird[:3])
        notes.append(f"contains {sum(n for _, n in weird)} non-ASCII characters: {shown}")
        notes.append("That is what a masked secret looks like when it is copied "
                     "from a display that hides it. Copy the key from the source "
                     "that holds the real value, not from a screen showing dots.")
    if "\n" in value or " " in value:
        notes.append("contains whitespace or a line break, so the assignment is "
                     "spilling past its own line")
    notes.append(f"{len(value)} characters, {len(value.encode('utf-8'))} bytes")
    return notes


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
            print(f"  {name}: MALFORMED  {_mask(value)}")
            print(f"    {hint}")
            for note in describe_bad(value):
                print(f"    {note}")
            print(f"    Repair it with:  python -m src.env_check --set {name}=<value>")
    return problems


def set_value(assignment: str, force: bool = False) -> None:
    """Rewrite one variable safely, collapsing any duplicates to a single line."""
    if "=" not in assignment:
        raise SystemExit("expected NAME=value")
    name, _, value = assignment.partition("=")
    name, value = name.strip(), value.strip().strip('"').strip("'")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit(f"{name!r} is not a valid variable name")
    if "\n" in value:
        raise SystemExit("value must be a single line")

    # Refuse before writing. Writing a bad value and warning about it leaves
    # the file worse than it was and the failure surfaces three steps later.
    check = CHECKS.get(name)
    if check and value and not check[0].match(value) and not force:
        print(f"REFUSED: that value is not a valid {name}.", file=sys.stderr)
        print(f"  {check[1]}", file=sys.stderr)
        for note in describe_bad(value):
            print(f"  {note}", file=sys.stderr)
        print("  .env was left unchanged. Re-copy the value and try again "
              "(--force overrides).", file=sys.stderr)
        raise SystemExit(1)

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


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", dest="print_var", metavar="NAME",
                   help="print one value, for use by shell scripts")
    p.add_argument("--set", dest="assignment", metavar="NAME=VALUE",
                   help="write one value safely, collapsing duplicates")
    p.add_argument("--force", action="store_true",
                   help="write even if the value fails its format check")
    args = p.parse_args(argv)

    if args.assignment:
        set_value(args.assignment, force=args.force)
        return
    if args.print_var:
        sys.stdout.write((load().get(args.print_var) or "").strip())
        return
    raise SystemExit(1 if report() else 0)


if __name__ == "__main__":
    main()
