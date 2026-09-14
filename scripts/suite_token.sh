#!/usr/bin/env bash
# Set up (or rotate) the test board's shared secret.
#
# The board's ingest is the one /system/* route a person does not open:
# a finished pytest process posts what it did, and it has no session to
# do that with.  It carries this secret instead, so the secret IS the
# wall — the route answers 401 from the internet, not 404.
#
# What the secret guards, sized rather than assumed: a holder can WRITE
# fake runs (ten a minute, five hundred failures each) and can READ
# nothing — every read on the board is behind require_system_owner.  So
# losing it costs a polluted board and some rows, pruned at ninety days.
#
# The token is never printed.  A secret echoed to a terminal is a secret
# in the scrollback, in the tmux buffer and in whatever recorded the
# session; this writes it straight to .env, which is already 0600 and
# first in .gitignore.
#
#     scripts/suite_token.sh            # set it up if it is not set
#     scripts/suite_token.sh --rotate   # replace an existing one
#     scripts/suite_token.sh --check    # is the board reachable and the token right?
#
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root/.env"
url_default="http://127.0.0.1:8000/api/system/suite/runs"
mode="${1:-setup}"

die() { echo "error: $*" >&2; exit 1; }

[ -f "$env_file" ] || die "no .env at $env_file"

# The file is about to hold a secret; refuse to add one to a file others
# can read rather than fixing it silently and leaving the window open.
perms="$(stat -c '%a' "$env_file")"
[ "$perms" = "600" ] || die ".env is mode $perms, expected 600 — run: chmod 600 $env_file"

current_token="$(grep -E '^SUITE_REPORT_TOKEN=' "$env_file" | tail -1 | cut -d= -f2- || true)"
current_url="$(grep -E '^SUITE_REPORT_URL=' "$env_file" | tail -1 | cut -d= -f2- || true)"

# ── --check: is the board reachable, and does this token open it? ─────
#
# Sends a DELIBERATELY malformed body, so nothing is ever written: a
# wrong token is refused before the body is read (401), a right one gets
# as far as validating it (422).  The two answers tell the whole story
# without putting a row on the board.
if [ "$mode" = "--check" ]; then
    [ -n "$current_token" ] || die "SUITE_REPORT_TOKEN is not set — run this script with no arguments first"
    url="${current_url:-$url_default}"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            -X POST -H 'Content-Type: application/json' \
            -H "X-Suite-Token: $current_token" \
            -d '{"not":"a run"}' "$url" || echo 000)"
    case "$code" in
        422) echo "ok — the board is reachable and the token is accepted"
             echo "   (422 is the ingest refusing a deliberately malformed body;"
             echo "    nothing was written)"; exit 0 ;;
        401) die "the board answered 401 — this token is not the one the API has.
   The API reads SUITE_REPORT_TOKEN at request time from its own
   environment, so if you changed .env, restart it:
       sudo systemctl restart 4truck-api" ;;
        404) die "404 at $url — the API is running an older build that has no
   board yet.  Restart it:  sudo systemctl restart 4truck-api" ;;
        000) die "could not reach $url — is the API up? (systemctl status 4truck-api)" ;;
        *)   die "unexpected answer $code from $url" ;;
    esac
fi

# ── setup / rotate ───────────────────────────────────────────────────
if [ -n "$current_token" ] && [ "$mode" != "--rotate" ]; then
    echo "SUITE_REPORT_TOKEN is already set in .env — leaving it alone."
    echo
    echo "  To replace it:        scripts/suite_token.sh --rotate"
    echo "  To test what is set:  scripts/suite_token.sh --check"
    exit 0
fi

command -v openssl >/dev/null || die "openssl not found"
token="$(openssl rand -hex 32)"
[ "${#token}" -eq 64 ] || die "openssl produced ${#token} chars, expected 64"

backup="$env_file.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$env_file" "$backup"

# Rewrite in place through a 0600 temp file in the same directory, so the
# secret never exists in a world-readable temp dir and the swap is atomic.
tmp="$(mktemp "$env_file.XXXXXX")"
chmod 600 "$tmp"
{
    grep -vE '^SUITE_REPORT_(TOKEN|URL)=' "$env_file" || true
    echo "SUITE_REPORT_TOKEN=$token"
    echo "SUITE_REPORT_URL=${current_url:-$url_default}"
} > "$tmp"
mv "$tmp" "$env_file"

if [ "$mode" = "--rotate" ]; then
    echo "rotated. The old value is in $backup"
else
    echo "set. .env backed up to $backup"
fi
echo
echo "The token is not printed here on purpose — a secret echoed to a"
echo "terminal lives in the scrollback."
echo
echo "Next:"
echo "  1. sudo systemctl restart 4truck-api     # the API reads the new value"
echo "  2. scripts/suite_token.sh --check        # confirm it took"
echo "  3. run pytest — the run lands on system.4truck.us → Test board"
if [ "$mode" = "--rotate" ]; then
    echo
    echo "Anything else holding the OLD token (a CI secret, another"
    echo "machine) now logs one line per run and changes nothing else —"
    echo "the reporter never fails a run because the board refused it."
fi
