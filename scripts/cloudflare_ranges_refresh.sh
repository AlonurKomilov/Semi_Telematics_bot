#!/usr/bin/env bash
# Regenerate nginx's Cloudflare trust list from Cloudflare's published ranges.
#
# Why this exists: conf.d/cloudflare-realip.conf names the edges whose
# CF-Connecting-IP we believe and (via snippets/cf-only.conf) the ONLY peers
# the 4truck vhosts will talk to.  Cloudflare changes those ranges rarely,
# but when it does, a visitor landing on a new edge gets 444 until this
# list catches up.  So: fetch, validate, regenerate, and reload nginx only
# if something actually changed.
#
# Usage:  sudo scripts/cloudflare_ranges_refresh.sh            # live
#         scripts/cloudflare_ranges_refresh.sh --dry-run FILE  # write FILE, touch nothing
# Cron (root, monthly):  0 4 1 * * /home/abcdev/projects/Semi_Telematics_bot/scripts/cloudflare_ranges_refresh.sh
set -euo pipefail

LIVE=/etc/nginx/conf.d/cloudflare-realip.conf
DRY=""; [ "${1:-}" = "--dry-run" ] && DRY="${2:?--dry-run needs an output path}"

v4=$(curl -fsS -m 20 https://www.cloudflare.com/ips-v4)
v6=$(curl -fsS -m 20 https://www.cloudflare.com/ips-v6)

OUT=$(mktemp); trap 'rm -f "$OUT"' EXIT
V4="$v4" V6="$v6" python3 - > "$OUT" <<'PY'
import ipaddress, os, sys
ranges = []
for blob in (os.environ["V4"], os.environ["V6"]):
    for line in blob.splitlines():
        line = line.strip()
        if not line: continue
        ipaddress.ip_network(line)              # a malformed line aborts the run
        ranges.append(line)
# A truncated or error-page fetch must never shrink the trust list.
if len(ranges) < 20:
    sys.exit(f"refusing: only {len(ranges)} ranges fetched (expected ~22)")
out = ["# /etc/nginx/conf.d/cloudflare-realip.conf",
       "# realip: trust CF-Connecting-IP ONLY when the TCP peer is a Cloudflare edge.",
       "# After this, $remote_addr = true client; $realip_remote_addr = the edge (original peer)."]
out += [f"set_real_ip_from {r};" for r in ranges]
out += ["real_ip_header CF-Connecting-IP;", "",
        "# geo on the ORIGINAL peer (pre-realip). 1 = via Cloudflare (or this box itself).",
        "geo $realip_remote_addr $from_cloudflare {", "    default 0;",
        "    127.0.0.1 1;", "    ::1 1;"]
out += [f"    {r} 1;" for r in ranges] + ["}"]
print("\n".join(out))
PY

if [ -n "$DRY" ]; then cp "$OUT" "$DRY"; echo "dry-run: wrote $DRY ($(grep -c set_real_ip_from "$DRY") ranges)"; exit 0; fi

if [ -f "$LIVE" ] && diff -q "$OUT" "$LIVE" >/dev/null; then
    echo "cloudflare-realip.conf unchanged"; exit 0
fi
[ "$(id -u)" = 0 ] || { echo "ranges changed — rerun as root to apply" >&2; exit 2; }
cp "$LIVE" "$LIVE.bak.$(date +%Y%m%d%H%M)" 2>/dev/null || true
cp "$OUT" "$LIVE"
nginx -t
systemctl reload nginx
echo "cloudflare-realip.conf updated + nginx reloaded"
