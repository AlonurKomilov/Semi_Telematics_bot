# System operator console rollout runbook

The operator-only dashboard at **`system.4truck.us`** provides:

- Cross-account accounts list with search + filters
- Per-account detail with live billing math, subscription state, and
  recent invoices
- Comp grant / renew / revoke + audit log
- Force Stripe extras-qty sync (manual run of the scheduler job)
- Refresh vehicles from Samsara (manual ingest)
- Billing-email override (operator-only; customer dashboard cannot edit)

System-wide stats card is the landing view.  This runbook covers
first-time deploy + access control + where to add new surfaces.

## What this is (and isn't)

- **Is**: a separate React SPA built from `interfaces/system_dashboard/`,
  served at `system.4truck.us` only, used by 4truck platform operators
  for cross-account billing actions.
- **Is not**: a customer feature.  No customer ever sees it.  No customer
  code links to it.  No customer JWT can call its endpoints.

The customer dashboard (`dash.4truck.us`) and this operator console
share the same `/api/*` backend but **separate JS bundles**, **separate
localStorage**, and **separate hostnames** so an XSS / token leak on
one side cannot pivot to the other.

## Prerequisites

1. **DNS** — `system.4truck.us` A record pointing at the origin IP
   (proxied through Cloudflare orange-cloud, like all the other
   subdomains).  ✅ already in place per the rollout screenshot.

2. **TLS** — `*.4truck.us` wildcard cert covers it; same
   `/etc/nginx/ssl/4truck.crt` no rotation needed.

3. **`SYSTEM_OWNER_IDS`** in `.env` — comma-separated list of Telegram
   user_ids that can call `/system/*` endpoints.  Example:

       SYSTEM_OWNER_IDS=12345,67890

   This is checked by the `require_system_owner` dep at every admin
   request.  Without it, every `/system/*` call returns HTTP 403.

3a. **Two separate Telegram bots** in `.env` — see [.env.example](../../.env.example):

       TELEGRAM_SYSTEM_BOT_TOKEN=…  # SYSTEM bot — system.4truck.us login + ops
       TELEGRAM_LOGIN_BOT_TOKEN=…   # CUSTOMER bot — dash./app./fleet./…

   (The legacy name ``TELEGRAM_BOT_TOKEN`` is still accepted as a
   fallback for the system token but is deprecated — rename to
   ``TELEGRAM_SYSTEM_BOT_TOKEN``.)

   The system console at `system.4truck.us` validates the Telegram
   Login Widget against `TELEGRAM_SYSTEM_BOT_TOKEN` exclusively — never
   the login bot, never a per-account bot.  The customer dashboards do
   the opposite.  This split keeps the two audiences cryptographically
   separate even if one token leaks.

   **In @BotFather** (`/setdomain`) — Telegram allows only ONE Login
   Widget domain per bot:
   - SYSTEM bot → `system.4truck.us`
   - CUSTOMER login bot → `4truck.us` (the apex is the single login
     gate — the SPA's `/login` route is served at
     `https://4truck.us/login`, then redirects to the matching persona
     subdomain after auth.  The `.4truck.us`-scoped SSO cookie carries
     the session across `dash./fleet./dispatch./safety.`, so the apex
     domain covers every persona host with one BotFather entry.)

4. **Cloudflare IP allowlist** for `system.4truck.us` — done in the
   Cloudflare dashboard:
   - Security → WAF → Custom Rules
   - Field: Hostname equals `system.4truck.us`
   - Action: Block, except `(ip.src in {your.ip.list})`
   - This is **defense-in-depth** on top of `SYSTEM_OWNER_IDS`; even
     the most leaked operator JWT can't be used from an unlisted IP.

5. **Build the SPA** at least once:

       make system-dashboard-build

   First run takes longer (npm install of ~10 packages); subsequent
   builds are ~10-15s.

## Deploy

1. Pull the code on the origin server.
2. Build the SPA:

       make system-dashboard-build

3. Install the nginx vhost (the `nginx/4truck.conf` already includes
   the `server_name system.4truck.us` block):

       sudo -v
       make nginx-install

4. Restart the API so the new `/system/*` routes register:

       sudo make restart-api

5. Visit `https://system.4truck.us` from an allowlisted IP, log in with
   Telegram.  The login page verifies your access by probing
   `/api/system/stats`; if 403, you'll get "not on operator allowlist".

## File map

| Path | What |
|---|---|
| `interfaces/api/routes/system.py` | `/system/accounts`, `/system/accounts/{id}`, `/system/stats` — server-side admin endpoints |
| `interfaces/api/deps.py::require_system_owner` | The dep every `/system/*` route uses to gate by `SYSTEM_OWNER_IDS` |
| `interfaces/api/routes/billing.py` | Comp grant/renew/revoke/history — already gated by the same allowlist; the operator console calls these directly |
| `interfaces/system_dashboard/src/` | React + Vite + Tailwind SPA |
| `interfaces/system_dashboard/dist/` | Build output served by nginx |
| `nginx/4truck.conf` | `server { server_name system.4truck.us … }` block |

## Auth model

```
Operator opens system.4truck.us
        ↓
Telegram login widget (same /api/auth/telegram-login endpoint)
        ↓
JWT returned in response body (not used as a cookie here)
        ↓
Stored in localStorage on system.4truck.us origin
        ↓
Every /api/* request: Authorization: Bearer <token>
        ↓
require_system_owner dep:
    - decodes JWT, reads telegram_id
    - checks telegram_id in SYSTEM_OWNER_IDS env
    - if no → 403
    - if yes → request proceeds
```

**No cookies set on system.4truck.us** — the JWT lives in localStorage
which the browser scopes per-origin.  A compromise on `dash.4truck.us`
cannot read or write `system.4truck.us`'s localStorage.

The customer-side dashboard does still set an apex-scoped cookie
(`.4truck.us` family) for cross-subdomain SSO between
`dash./fleet./dispatch./safety.`.  That cookie scope intentionally
**excludes** `system.` because the operator console does not set it.

## Pages

### `/login`
Telegram login widget.  Verifies operator membership immediately after
the JWT lands by probing `/api/system/stats` — failing fast if the user
isn't on the allowlist beats showing a half-loaded accounts page that
just 403s on every fetch.

### `/accounts` (default landing)
- 5 stat cards: total accounts, active, past-due, comped, by-tier
- Search box (name substring)
- Filters: status, tier, comp yes/no
- Table: id, name+slug, tier, status, vehicle count, provider, flags, created

### `/accounts/:id`
- Subscription card with all provider ids (visible for ops debugging)
- Live billing computation (active vs inactive vehicles + extras math)
- **Operator actions**:
  - Force Stripe extras-qty sync — runs `sync_billing_quantity` now
  - Refresh vehicles from Samsara — re-pulls fleet overview, recomputes
  - Billing email override — write locally + push to Stripe Customer
- Comp section with Grant / Renew / Revoke actions and full audit log
- Recent invoices (Stripe-mirrored) with hosted-URL links

### Operator action endpoints

These mutate state on a specific account; every call is logged with
the operator's `telegram_id`:

| Endpoint | What | When to use |
|---|---|---|
| `POST /api/system/accounts/{id}/sync-quantity` | Trigger `sync_billing_quantity` immediately | Customer reports a bill discrepancy; want the Stripe extras qty to land before the next 60s tick |
| `POST /api/system/accounts/{id}/refresh-vehicles` | Re-ingest from Samsara + recompute | New vehicles added at Samsara, dashboard shows stale count |
| `PATCH /api/system/accounts/{id}/billing-email` | Operator override of `billing_email` | Customer's billing contact changed, customer-side dashboard cannot edit |

Each is `Depends(require_system_owner)`-gated.  The refresh action
blocks for ~3–5 s (Samsara round-trip) — that's intentional so the
operator sees the new active count synchronously.

## Adding new surfaces

1. **Backend**:  add an endpoint to `interfaces/api/routes/system.py`,
   gate it with `Depends(require_system_owner)`.  Don't put cross-
   account ops on the existing `/admin/*` router — that one is
   per-tenant.

2. **Frontend**:  add a page under `interfaces/system_dashboard/src/pages/`
   and a route in `App.tsx`.  Reuse the `apiJSON` client + the type
   shapes in `src/types.ts`.

3. **Rebuild + redeploy**:

       make system-dashboard-build
       sudo make restart-api      # only needed if backend changed

   No nginx reload is necessary for code-only changes — nginx serves
   the dist directory directly.

## Common pitfalls

- **403 on every API call after login** — operator's Telegram id isn't
  in `SYSTEM_OWNER_IDS`.  Edit `.env`, restart the API, log out + log
  back in (the JWT itself doesn't carry the operator bit; the env
  allowlist is re-checked on every request).
- **Telegram widget doesn't render** — `/api/auth/system-config` failed.
  Check that `TELEGRAM_SYSTEM_BOT_TOKEN` (the SYSTEM bot, not the login
  bot) is set in `.env` and the API has restarted since.  Also confirm
  the SYSTEM bot's `/setdomain` in @BotFather is `system.4truck.us`.
- **"Bot domain invalid"** in the widget — the SYSTEM bot's Login Widget
  domain in @BotFather isn't `system.4truck.us`.  Run `/setdomain` in
  @BotFather, select the bot returned by
  `curl -s https://system.4truck.us/api/auth/system-config`, set it to
  `system.4truck.us`.
- **"Failed to fetch" / CORS errors** — nginx vhost not installed or
  not reloaded.  Check `sudo nginx -t` and `sudo systemctl reload nginx`.
- **Operator action buttons missing or 404** — the SPA bundle is stale.
  Run `make system-dashboard-build` and reload the page.

## Rollback

The operator console can be fully disabled without touching customer
code:

1. **Soft (recommended)** — remove `system.4truck.us` from the
   Cloudflare allowlist; no IP can reach the origin.
2. **Hard** — remove the `server { server_name system.4truck.us … }`
   block from `nginx/4truck.conf`, run `make nginx-install`.  nginx
   then 404s every `system.4truck.us` request.
3. **Backend** — leave `/system/*` and `require_system_owner` in
   place; they're harmless without a UI to drive them.

The schema, env vars, and customer dashboard are untouched by any of
this — a rollback of the operator console has zero customer impact.
