# Phase 4 Rollout — Cloudflare CDN for static SPA

Puts Cloudflare in front of `4truck.us` and `safety-dashboard.xyz` so
hashed Vite asset bundles (`/dashboard/assets/*`, `/miniapp/assets/*`)
serve from the edge — first-page-load TTFB drops 100-300 ms globally,
origin sees ~80% fewer requests for static files.

API routes (`/api/*`) and the Telegram webhook (`/webhook`) **bypass
the edge cache entirely** — those are auth-sensitive and time-critical.

Zero application changes once deployed; the rollback is a Cloudflare
DNS toggle (orange cloud → grey cloud).

---

## Cache strategy (one-line summary)

| Path | Cache lifetime | Why |
|---|---|---|
| `/dashboard/assets/*`, `/miniapp/assets/*` | **forever** (`max-age=31536000, immutable`) | Vite emits hashed filenames; new deploys produce new filenames |
| `/dashboard/index.html`, `/miniapp/index.html` | **60s + 5min stale-while-revalidate** | New deploys propagate within 1 min; SWR keeps users on a working shell during revalidation |
| `/api/*` | **never** (`no-store`) | Auth-scoped, per-tenant data |
| `/webhook` | **never** | Telegram POSTs |

---

## What ships with Phase 4 (origin side)

| Component | File | Purpose |
|---|---|---|
| Cache headers in nginx | [nginx/4truck.conf](../../nginx/4truck.conf), [nginx/semi-telematics-bot.conf](../../nginx/semi-telematics-bot.conf) | New `location ~ ^/dashboard/assets/`, `location = /dashboard/index.html`, mirrored for `/miniapp/`. `add_header Cache-Control ... always` so the directive survives 404/304 responses too. |
| API no-store middleware | [interfaces/api/app.py](../../interfaces/api/app.py) | `ApiNoStoreMiddleware` stamps `Cache-Control: no-store, no-cache, must-revalidate, private` on every `/api/*` response that didn't set its own Cache-Control. Defensive — even a misconfigured Cloudflare Page Rule can't cache a tenant payload. |
| Cloudflare config | (this runbook) | DNS, Page Rules, Cache Rules — set in the Cloudflare dashboard (no code) |

---

## Pre-flight

### 1. Confirm the new nginx headers locally
```bash
sudo nginx -t                                  # syntax OK
sudo systemctl reload nginx
curl -sI https://4truck.us/dashboard/assets/index-XXXX.js | grep -i cache-control
# expect: Cache-Control: public, max-age=31536000, immutable

curl -sI https://4truck.us/dashboard/index.html | grep -i cache-control
# expect: Cache-Control: public, max-age=60, stale-while-revalidate=300

curl -sI https://4truck.us/api/health | grep -i cache-control
# expect: Cache-Control: no-store, no-cache, must-revalidate, private
```
If the API one is missing, the middleware isn't loaded — restart `4truck-api`.

### 2. Check `add_header ... always` is in place
Without `always`, nginx drops the header on 304/404/etc. Cloudflare
respects `Cache-Control` only when present, so missing it on a 304
would silently default to "don't cache, refetch every time" — exactly
the wrong outcome.

```bash
grep -c "always" nginx/4truck.conf nginx/semi-telematics-bot.conf
# expect: 4 each (2 SPAs × 2 location blocks per SPA)
```

---

## Cloudflare dashboard setup (one-time)

### A. DNS
1. Sign in to Cloudflare → pick the `4truck.us` zone
2. **DNS → Records**: ensure the apex `A` record (and `www` CNAME) point at the origin IP
3. Click the cloud icon next to each record to **Orange (proxied)**
4. SSL/TLS → Overview → set to **Full (Strict)** (Cloudflare Origin CA cert is already deployed at `/etc/nginx/ssl/4truck.crt` per `4truck.conf`)
5. Repeat for the `safety-dashboard.xyz` zone

### B. Page Rules → Cache Rules (preferred new format)
Create three rules in priority order (1 = highest):

#### Rule 1 — Bypass cache for API + webhook
- **When incoming requests match**:
  `(http.request.uri.path matches "^/api/") or (http.request.uri.path eq "/webhook")`
- **Then**:
  - Cache eligibility: **Bypass cache**

#### Rule 2 — Cache hashed SPA assets forever
- **When incoming requests match**:
  `(http.request.uri.path matches "^/dashboard/assets/") or (http.request.uri.path matches "^/miniapp/assets/") or (http.request.uri.path matches "^/app/assets/")`
- **Then**:
  - Cache eligibility: **Eligible for cache**
  - Edge TTL: **Override origin / 1 year**
  - Browser TTL: **Respect existing headers**

#### Rule 3 — Short-cache shell HTML
- **When incoming requests match**:
  `(http.request.uri.path eq "/dashboard/") or (http.request.uri.path eq "/dashboard/index.html") or (http.request.uri.path eq "/miniapp/") or (http.request.uri.path eq "/miniapp/index.html")`
- **Then**:
  - Cache eligibility: **Eligible for cache**
  - Edge TTL: **60 seconds**
  - Browser TTL: **Respect existing headers**

### C. Security
- **Security → Bots**: enable **Bot Fight Mode** (free) — keeps obvious scrapers off origin
- **Security → WAF → Managed Rules**: enable the OWASP Core Ruleset (free for Pro plans)
- **Security → DDoS** (free) — already on by default

### D. Speed
- **Speed → Optimization → Auto Minify**: turn OFF for JS/CSS (Vite already minifies and Cloudflare's minifier sometimes breaks ES2020+ syntax)
- **Speed → Optimization → Brotli**: ON (free, drops asset size 15-25%)

---

## Rollout

### Stage 1 — origin nginx changes (already shipped in this commit)
```bash
git pull
sudo cp nginx/4truck.conf /etc/nginx/sites-available/4truck
sudo cp nginx/semi-telematics-bot.conf /etc/nginx/sites-available/semi-telematics-bot
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart 4truck-api      # picks up ApiNoStoreMiddleware
```

Validate with the curl commands from "Pre-flight #1".

### Stage 2 — Cloudflare DNS
1. Lower DNS TTL on the apex A record to 60s **24h before flipping**
   (so rollback DNS propagates fast)
2. Click the orange-cloud (proxy) toggle on the A record
3. Wait 60s, then:
   ```bash
   dig 4truck.us +short
   # expect: a Cloudflare IP (104.x or 172.x), NOT your origin
   curl -sI https://4truck.us/dashboard/assets/index-XXXX.js | grep -i cf-cache-status
   # first hit: "DYNAMIC" or "MISS"; second hit (within 1s): "HIT"
   ```

### Stage 3 — observe for 24 h
- Cloudflare Analytics → Caching: watch the "Cached requests" ratio for `/dashboard/assets/*`. Expect ≥ 80% within an hour, ≥ 95% within 24h.
- nginx access log on origin:
  ```bash
  awk '/dashboard\/assets/ {n++} END {print n}' /var/log/nginx/access.log
  # before-flip baseline / after: drops ~10×
  ```
- User-reported "old version showing": verify Stage 4 (purge) before flipping bigger zones.

### Stage 4 — first deploy after CDN
- Vite emits new hashed filenames; Cloudflare caches them on first request
- The shell `/dashboard/index.html` Edge TTL = 60s, so within 1 min every region serves the new shell
- For instant promotion: **Caching → Configuration → Purge Cache → Custom Purge → URLs**: paste `https://4truck.us/dashboard/index.html` and submit

---

## Verification

### Cache headers are correct end-to-end
```bash
# Browser-visible (after Cloudflare proxy)
curl -sI https://4truck.us/dashboard/assets/index-XXXX.js | grep -E "(cache-control|cf-cache-status)"
# expect:
#   cache-control: public, max-age=31536000, immutable
#   cf-cache-status: HIT  (after first request)

curl -sI https://4truck.us/dashboard/index.html | grep -E "(cache-control|cf-cache-status)"
# expect:
#   cache-control: public, max-age=60, stale-while-revalidate=300
#   cf-cache-status: HIT or REVALIDATED

curl -sI https://4truck.us/api/health | grep -E "(cache-control|cf-cache-status)"
# expect:
#   cache-control: no-store, no-cache, must-revalidate, private
#   cf-cache-status: BYPASS  (Page Rule honored)
```

### Authenticated payloads not cached
```bash
# Hit /api/admin/users twice, second hit should still be 200 not 304
TOKEN="..."
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  https://4truck.us/api/admin/users
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  https://4truck.us/api/admin/users
# expect 200, 200 (no cache-mediated 304)
```

### Origin traffic dropped
```bash
# On the origin host, count nginx asset hits in the last hour
awk -v cutoff="$(date -d '1 hour ago' '+%d/%b/%Y:%H')" '
  $4 >= "["cutoff && /\/dashboard\/assets\// {n++}
  END {print n}
' /var/log/nginx/access.log
# expect: drops by ≥ 80% from pre-CDN baseline
```

### Lighthouse score improved
Open Chrome DevTools → Lighthouse → "Performance" on `/dashboard/`:
- TTFB: was 200-500ms, expect 50-150ms after CDN warmup
- LCP: drops 100-300ms

---

## Monitoring (24h after rollout)

| Signal | Where | Healthy |
|---|---|---|
| Cloudflare cache hit ratio | CF Analytics → Caching | ≥ 95% on `/dashboard/assets/*` |
| Origin asset request rate | nginx access log | ≤ 5% of pre-CDN baseline |
| API status codes | CF Analytics → Workers / Status codes | unchanged from origin (CF is bypassing them) |
| `cf-cache-status: BYPASS` on `/api/` | curl spot-check | always BYPASS |
| User reports "old version" | support tickets | zero (after first 60s post-deploy) |

---

## Rollback

### Soft rollback — flip the orange cloud
**Cloudflare → DNS → click the orange cloud → grey cloud.** DNS now
returns origin IP; CDN out of the path; nginx serves SPAs directly.
Takes effect in ≤ DNS TTL (60s if you lowered it pre-flip).

### Hard rollback — remove cache headers
If the `Cache-Control: max-age=31536000` header itself is the problem
(e.g. a non-hashed file accidentally served from `/assets/` and now
stuck in browsers' caches for a year):
```bash
# Edit nginx/4truck.conf to remove the immutable header
git revert <phase-4-commit>
sudo cp nginx/4truck.conf /etc/nginx/sites-available/4truck
sudo nginx -t && sudo systemctl reload nginx
# Purge Cloudflare's cached copy:
# CF dashboard → Caching → Purge Everything (nuclear) or specific URL
```

The `ApiNoStoreMiddleware` is purely additive — leave it in even on
rollback so authenticated payloads stay uncacheable forever.

---

## Tuning

**Edge TTL on shell HTML.** Default 60s; raise to 300s if you deploy
< daily and want lower origin hits, lower to 30s if you deploy hourly.

**Browser TTL on assets.** Default `max-age=31536000` is safe because
filenames are content-addressed. Don't raise it (already at year max);
don't lower it without a strong reason.

**Cache eligibility for `/api/health`.** If origin nginx 5xx storms
make Cloudflare's healthcheck flap, add a 30s edge TTL on
`/api/health` only — but make sure the endpoint is anonymous.

---

## Known limitations after Phase 4

- **No Cloudflare Workers** — all logic still on origin. Phase 5+ could
  add a Worker for `/api/health` to absorb heartbeat checks.
- **No image / file CDN** — uploaded files served by `/api/files/...`
  bypass the cache (correctly). If user-uploaded images become a hot
  path, consider Cloudflare R2 or a signed-URL pattern.
- **No KV / Durable Objects** — geofence lookups, POI overlays still
  hit origin. SWR cache (Phase 1) absorbs most of that already.
- **Cloudflare Free plan limits**: 3 Page Rules. The Cache Rules format
  has no such limit — use that one.
