# Subdomain rollout runbook

Cutover from the apex-path layout to dedicated subdomains for each surface:

| Old URL                          | New URL                       |
|----------------------------------|-------------------------------|
| `https://4truck.us/dashboard/`   | `https://dash.4truck.us/`     |
| `https://4truck.us/miniapp/`     | `https://app.4truck.us/`      |
| `https://4truck.us/api/`         | `https://api.4truck.us/` (strips `/api`) |
| `https://4truck.us/webhook`      | `https://bot.4truck.us/webhook` |

Apex paths 301-redirect to the new homes so existing bookmarks survive.

## Prerequisites

1. **DNS**: A/AAAA records for `dash`, `app`, `api`, `bot` in Cloudflare, all pointing to the same origin server, all Proxied (orange-cloud). ✅ (already set)
2. **TLS certificate**: the origin cert at `/etc/nginx/ssl/4truck.crt` must cover the new hostnames as Subject Alternative Names (SANs), OR be a wildcard `*.4truck.us`.  Verify with:

       openssl x509 -in /etc/nginx/ssl/4truck.crt -noout -text | grep -A1 'Subject Alternative'

   If the cert doesn't list the subdomains:
   - Cloudflare Dashboard → SSL/TLS → Origin Server → **Create Certificate**
   - Hostnames: `*.4truck.us`, `4truck.us`
   - Replace `/etc/nginx/ssl/4truck.crt` and `/etc/nginx/ssl/4truck.key`.

## Cutover sequence (one-time, in order)

### 1. Build dashboards with the new Vite base

       make dashboard-build
       make miniapp-build

   `vite.config.js` defaults `base` to `/` now, so `dist/` is ready for the subdomain layout.  No env vars needed for the default case.

### 2. Install the new nginx config

       sudo -v
       make nginx-install

   This copies `nginx/4truck.conf` to `/etc/nginx/sites-available/4truck` and reloads.  `nginx -t` is run automatically.

### 3. Update environment variables in `.env`

       WEBAPP_URL=https://app.4truck.us
       WEBHOOK_URL=https://bot.4truck.us/webhook
       GDRIVE_OAUTH_REDIRECT_URI=https://api.4truck.us/storage/google/callback

   (Note: under `api.4truck.us` nginx strips the `/api` prefix, so the redirect URI drops `/api/`.)

### 4. Restart the API + bot

       make restart

### 5. Re-register the bot webhook with Telegram

   For the **system bot** (the global `TELEGRAM_BOT_TOKEN`):

       curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
         -d url=https://bot.4truck.us/webhook \
         -d secret_token=$WEBHOOK_SECRET

   For **per-account bots** — the registry in `infra/bot_registry.py` calls `setWebhook` on each account's `start_bot()`.  Restarting the bot service (`make restart-bot`) triggers a re-registration loop for every active account.  Confirm with:

       curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo

### 6. Update the Mini App URL in @BotFather

   For the system bot:
   - Open @BotFather → `/mybots` → select bot → **Bot Settings** → **Menu Button** → **Configure menu button**
   - URL: `https://app.4truck.us`

   For per-account bots — the operator who registered each bot needs to do this in their own @BotFather menu.  (We can't do it for them because @BotFather is interactive.)

### 7. Update Google OAuth redirect URI

   - Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID
   - **Authorized redirect URIs**: add `https://api.4truck.us/storage/google/callback`
   - Keep the old `https://4truck.us/api/storage/google/callback` URI for a couple of weeks so any in-flight OAuth flows that started against the apex can complete; remove it after verifying logs show no traffic.

## Verification checklist

After the cutover, confirm each URL serves correctly:

       curl -sIL https://dash.4truck.us/ | grep HTTP             # → 200
       curl -sIL https://app.4truck.us/ | grep HTTP              # → 200
       curl -sIL https://api.4truck.us/health | grep HTTP        # → 200
       curl -sIL https://4truck.us/dashboard/fleet/vehicles | grep -E '(HTTP|Location)'
       # ↑ should show:  301 → https://dash.4truck.us/fleet/vehicles  then  200
       curl -sIL https://4truck.us/miniapp/ | grep -E '(HTTP|Location)'
       # ↑ should show:  301 → https://app.4truck.us/  then  200
       curl -sIL https://4truck.us/privacy | grep HTTP           # → 200 (unchanged)
       curl -sIL https://4truck.us/terms | grep HTTP             # → 200 (unchanged)

## Rollback

If something breaks within minutes:

1. nginx config: `make nginx-install` now auto-backs-up the previous live config to `/etc/nginx/sites-available/4truck.<timestamp>.bak` and symlinks the most-recent one to `4truck.bak`.  Roll back with:

       sudo cp /etc/nginx/sites-available/4truck.bak /etc/nginx/sites-available/4truck
       sudo nginx -t && sudo systemctl reload nginx

2. Re-run `setWebhook` against `https://4truck.us/webhook` (or whatever the previous URL was — `getWebhookInfo` tells you what it is now).
3. In @BotFather, set the Mini App menu button URL back to the previous one.
4. In Google Cloud Console, the old `https://4truck.us/api/storage/google/callback` redirect URI is still authorized (per step 7 above — keep it around until the new URI bakes), so OAuth still works against the apex during rollback.
3. Revert `WEBAPP_URL` to `https://4truck.us/miniapp/`.

For a hash-rotated SPA cache miss, `lazyWithReload` in [dashboard router](../../interfaces/dashboard/src/router.tsx) auto-reloads on chunk-load failure; users recover on next refresh.

## Phase 2 — persona subdomains (`fleet.` / `dispatch.` / `safety.`)

Adds branded entry points that pre-select the matching role-shell view on first load.  The SPA bundle is the same — `RoleViewContext` inspects `window.location.hostname` and chooses the initial `activeView`.  A localStorage choice still wins, so operators can override.

Constraints:
- Only Owner/Admin (the switchable roles) see the auto-switch.  A real Fleet user landing on `dispatch.` keeps Fleet view — the subdomain is a hint, not a permission grant.
- Auth is still per-host localStorage.  An Owner already logged in on `dash.` will need to log in again on `fleet.` (cross-subdomain cookie auth is still future work).

### Cutover

1. **DNS** — add three Cloudflare A records (Proxied / orange-cloud) pointing to the same origin:

       fleet     → <origin IP>
       dispatch  → <origin IP>
       safety    → <origin IP>

   Confirm propagation:

       dig +short fleet.4truck.us dispatch.4truck.us safety.4truck.us

2. **TLS** — the cert at `/etc/nginx/ssl/4truck.crt` must cover the new hostnames.  If it's a wildcard `*.4truck.us` (recommended), nothing to do.  Otherwise reissue with the three new SANs (Cloudflare Origin CA → Create Certificate).

3. **Deploy nginx** — `nginx/4truck.conf` already adds the three hostnames to the `dash.` server block's `server_name` and to the HTTP→HTTPS redirect.  Apply with:

       sudo -v
       make nginx-install

4. **Deploy the dashboard build** — same `make dashboard-build`; no env changes.

### Verification

       curl -sIL https://fleet.4truck.us/    | grep HTTP   # → 200
       curl -sIL https://dispatch.4truck.us/ | grep HTTP   # → 200
       curl -sIL https://safety.4truck.us/   | grep HTTP   # → 200

In a browser, log in as an Owner on each persona host with a clean localStorage — the dashboard should land directly in Fleet / Dispatch / Safety view (left nav + hero strip match).  Then switch personas via the sidebar selector; the choice persists on next reload of that host.

### Rollback

Remove the three hostnames from `server_name` lines (both the `dash.` server block and the HTTP→HTTPS redirect), `make nginx-install`.  DNS records can stay — they just won't resolve to a valid vhost.

## What didn't change

- **JWT storage**: still localStorage on `dash.4truck.us` (not a cross-subdomain cookie yet).  The dashboard's API calls go to `dash.4truck.us/api/...` (same-origin via nginx proxy) so JWT survives.  Cross-subdomain auth shared across `fleet.` / `dispatch.` / `safety.` requires moving the JWT to a cookie scoped to `.4truck.us` — deferred until a user actually needs single sign-on across persona hosts.
- **Service worker**: the miniapp PWA's runtime caches still match `/api/*` patterns — works because miniapp's API calls go same-origin via `app.4truck.us/api/*`.
- **PWA scope**: `start_url` and `scope` in the manifest changed from `/miniapp/` to `/`.  Existing installs on Telegram WebView will need to be re-installed (the SW scope changed).  Most users haven't "installed" the Mini App anyway — it runs in WebView session.
