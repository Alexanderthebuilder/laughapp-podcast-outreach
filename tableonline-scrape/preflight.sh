#!/usr/bin/env bash
# Checks everything the run depends on, before spending hours on it.
set -uo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && . .venv/bin/activate
set -a; [ -f .env ] && . ./.env; set +a

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

echo "== Google Places (New) =="
if [ -z "${GOOGLE_PLACES_API_KEY:-}" ]; then
    bad "GOOGLE_PLACES_API_KEY not set in .env"
else
    body=$(curl -sS -m 30 -X POST "https://places.googleapis.com/v1/places:searchText" \
        -H "Content-Type: application/json" \
        -H "X-Goog-Api-Key: $GOOGLE_PLACES_API_KEY" \
        -H "X-Goog-FieldMask: places.id,places.displayName" \
        -d '{"textQuery":"Restaurant Aoi, Helsinki, Finland","maxResultCount":1}' 2>/dev/null)
    case "$body" in
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
                printf '        then add it to the key in the GCP console.\n'
            fi ;;
        *SERVICE_DISABLED*|*PERMISSION_DENIED*)
            bad "key rejected — is \"Places API (New)\" enabled for the project?" ;;
        *API_KEY_INVALID*) bad "key is invalid" ;;
        *displayName*)     ok "Places API (New) answered" ;;
        *)                 bad "unexpected response: $(echo "$body" | head -c 200)" ;;
    esac
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
