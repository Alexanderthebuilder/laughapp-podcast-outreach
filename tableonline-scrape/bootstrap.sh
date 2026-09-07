#!/usr/bin/env bash
# One-time VPS setup. Safe to re-run — every step is idempotent.
#
#   ./bootstrap.sh                      # set up, keep any existing .env
#   GOOGLE_PLACES_API_KEY=AIza... ./bootstrap.sh
set -euo pipefail

cd "$(dirname "$0")"
echo "==> setting up in $(pwd)"

# --- system packages ------------------------------------------------------
# python3-venv is not installed by default on Ubuntu server images, and
# whois is needed by the optional phase6 whois source.
if command -v apt-get >/dev/null 2>&1; then
    echo "==> installing system packages (apt)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip whois ca-certificates >/dev/null
fi

python3 --version

# --- virtualenv -----------------------------------------------------------
if [ ! -d .venv ]; then
    echo "==> creating virtualenv"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
echo "==> installing Python dependencies"
python -m pip install --quiet -r requirements.txt

# --- browser --------------------------------------------------------------
# --with-deps pulls the shared libraries headless Chromium needs; without it
# Playwright installs the browser and then fails to launch it.
echo "==> installing Chromium for Playwright (this one takes a few minutes)"
python -m playwright install --with-deps chromium

# --- configuration --------------------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> created .env from .env.example"
fi
if [ -n "${GOOGLE_PLACES_API_KEY:-}" ]; then
    python - "$GOOGLE_PLACES_API_KEY" <<'PY'
import pathlib, re, sys
key = sys.argv[1]
p = pathlib.Path(".env")
text = p.read_text()
if re.search(r"^GOOGLE_PLACES_API_KEY=.*$", text, re.M):
    text = re.sub(r"^GOOGLE_PLACES_API_KEY=.*$",
                  f"GOOGLE_PLACES_API_KEY={key}", text, flags=re.M)
else:
    text += f"\nGOOGLE_PLACES_API_KEY={key}\n"
p.write_text(text)
print("==> wrote GOOGLE_PLACES_API_KEY into .env")
PY
fi
chmod 600 .env

# --- verify ---------------------------------------------------------------
echo "==> running the offline test suite"
python -m pytest -q

echo
echo "=========================================================="
echo " Setup complete."
echo
echo " Activate the environment in any new shell with:"
echo "     cd $(pwd) && . .venv/bin/activate"
echo
echo " Then check connectivity and the API key:"
echo "     ./preflight.sh"
echo "=========================================================="
