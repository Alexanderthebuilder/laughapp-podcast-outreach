#!/usr/bin/env bash
# Checks everything the run depends on, before spending hours on it.
set -uo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && . .venv/bin/activate

fail=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

echo "== reachability =="
for host in www.tableonline.fi places.googleapis.com; do
    code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "https://$host/" 2>/dev/null)
    code=${code:-000}
    if [ "$code" = "000" ]; then bad "$host unreachable"; else ok "$host reachable (HTTP $code)"; fi
done

echo "== the /x/x/{id} shortcut =="
for id in 92 1528; do
    code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' \
        -A "Mozilla/5.0 (compatible; LaughAppResearchBot/1.0)" \
        "https://www.tableonline.fi/en/x/x/$id" 2>/dev/null)
    code=${code:-000}
    if [ "$code" = "200" ]; then ok "/en/x/x/$id -> 200"; else bad "/en/x/x/$id -> $code (expected 200)"; fi
done

echo "== .env =="
# Read .env with python-dotenv, exactly as the pipeline does. Shell-sourcing it
# here would apply different quoting rules, so preflight could pass on a file
# the real run reads differently.
if python -m src.env_check 2>&1 | sed 's/^/  /'; then :; else fail=1; fi
GOOGLE_PLACES_API_KEY=$(python -m src.env_check --print GOOGLE_PLACES_API_KEY 2>/dev/null)

echo "== Google Places (New) =="
if [ -z "${GOOGLE_PLACES_API_KEY:-}" ]; then
    bad "GOOGLE_PLACES_API_KEY not set in .env"
else
    body=$(curl -sS -m 30 -X POST "https://places.googleapis.com/v1/places:searchText" \
        -H "Content-Type: application/json" \
        -H "X-Goog-Api-Key: $GOOGLE_PLACES_API_KEY" \
        -H "X-Goog-FieldMask: places.id,places.displayName" \
        -d '{"textQuery":"Restaurant Aoi, Helsinki, Finland","maxResultCount":1}' 2>/dev/null)
    reason=$(printf '%s' "$body" | tr -d '\n' | sed -n 's/.*"reason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    message=$(printf '%s' "$body" | tr -d '\n' | sed -n 's/.*"message"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    case "$body" in
        *displayName*)
            ok "Places API (New) answered" ;;
        *API_KEY_IP_ADDRESS_BLOCKED*)
            ip=""
            for svc in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
                ip=$(curl -4 -sS -m 10 "$svc" 2>/dev/null | tr -d '[:space:]')
                [ -n "$ip" ] && break
            done
            bad "key is IP-restricted and this server is not allowed."
            if [ -n "$ip" ]; then
                printf '        Add this IPv4 to the key in the GCP console: \033[1m%s\033[0m\n' "$ip"
            else
                printf '        Find this server IP with:  curl -4 ifconfig.me\n'
            fi ;;
        *API_KEY_SERVICE_BLOCKED*)
            bad "the key exists but is not allowed to call this API."
            printf '        In the GCP console, open the key and either choose\n'
            printf '        "Do not restrict key" or add \033[1mPlaces API (New)\033[0m to its\n'
            printf '        API restrictions. Note it is a separate entry from the\n'
            printf '        legacy "Places API".\n' ;;
        *SERVICE_DISABLED*)
            bad "\"Places API (New)\" is not enabled on the project."
            printf '        Enable it at: https://console.cloud.google.com/apis/library/places.googleapis.com\n' ;;
        *API_KEY_INVALID*)
            bad "Google does not recognise the value .env is sending."
            printf '        Likely one of:\n'
            printf '          - the key was deleted, regenerated, or auto-disabled after being exposed\n'
            printf '          - .env holds a truncated or mistyped value\n'
            printf '        The key .env is currently sending (first/last 6 chars):\n'
            printf '          \033[1m%s...%s\033[0m  (length %s)\n' \
                "$(printf '%s' "$GOOGLE_PLACES_API_KEY" | cut -c1-6)" \
                "$(printf '%s' "$GOOGLE_PLACES_API_KEY" | rev | cut -c1-6 | rev)" \
                "$(printf '%s' "$GOOGLE_PLACES_API_KEY" | wc -c)"
            printf '        A Google API key is normally 39 characters and starts AIzaSy.\n' ;;
        *)
            bad "unexpected response from Places API" ;;
    esac
    [ -n "$reason" ]  && printf '        google reason:  %s\n' "$reason"
    [ -n "$message" ] && printf '        google message: %s\n' "$message"
fi

echo "== browser =="
if python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); b.close()" 2>/dev/null; then
    ok "Playwright Chromium launches"
else
    bad "Chromium will not launch — run: python -m playwright install --with-deps chromium"
fi

echo
if [ "$fail" = "0" ]; then
    echo "All checks passed. Next:  python -m src.phase1_enumerate crosscheck"
else
    echo "Fix the FAIL lines above before starting the run."
fi
exit "$fail"
