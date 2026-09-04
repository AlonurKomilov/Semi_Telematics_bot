#!/usr/bin/env bash
# Build one front-end and swap it into place.
#
# nginx serves ``interfaces/<app>/dist`` directly, and vite is
# configured with ``emptyOutDir: true`` — so a plain ``npm run build``
# DELETES the live directory first and then spends one to three minutes
# writing it back.  For that whole window every visitor gets a 404, and
# a deploy is something you have to time rather than just do.
#
# So: build into ``dist.staging``, then swap.  The swap is two renames
# microseconds apart instead of a minutes-long hole, and the previous
# build is kept until the new one is in place, so a failed build never
# takes the site with it.
#
#     scripts/build_spa.sh dashboard
#     scripts/build_spa.sh browser_extension sidepanel.html
#
# The second argument is the file that proves the build produced
# something loadable; it defaults to the SPA entry point, and the
# extension names its own because it has no index.html.
set -euo pipefail

app="${1:?usage: build_spa.sh <app under interfaces/> [entry-file]}"
entry="${2:-index.html}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="$root/interfaces/$app"
[ -d "$dir" ] || { echo "no such app: interfaces/$app" >&2; exit 2; }
cd "$dir"

rm -rf dist.staging dist.prev
npm run build -- --outDir dist.staging --emptyOutDir

# A build that "succeeded" without an entry point would swap a broken
# directory into the live path — check before touching what is serving.
[ -s "dist.staging/$entry" ] || {
    echo "build produced no dist.staging/$entry — leaving the live build alone" >&2
    rm -rf dist.staging
    exit 1
}

if [ -d dist ]; then mv dist dist.prev; fi
mv dist.staging dist
rm -rf dist.prev
