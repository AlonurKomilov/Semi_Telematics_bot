# External uptime monitoring — setup runbook

Why this exists: every internal alerter (ingest watchdog, capacity
alerts, error reporter) runs inside the bot on the one server. On
2026-08-04 the whole box slept ~20 hours and nothing alerted — the
thing that warns was the thing that was down. An external watcher is
the only fix for that class.

## What the code side provides (shipped)

`GET https://api.4truck.us/health` returns, always with HTTP 200:

```json
{"status":"ok","db":"ok","redis":"ok","bot":"ok"}
```

- `bot` comes from the capacity sampler's minute row (written inside
  the BOT process every 60s): `ok` when fresher than 3 minutes,
  `silent` when not, `unknown` when unreadable.
- `status` is `ok` only when DB is up AND the bot is not silent.
  So one keyword — `"status":"ok"` — covers server, nginx, API, DB,
  and the bot process.
- The operator console's Health page shows the same pulse as the
  `bot_process` component (ok ≤3 min · warn ≤10 · down after).

## UptimeRobot setup (owner, ~10 minutes, free tier)

1. Create an account at uptimerobot.com (free plan: 50 monitors,
   5-minute interval).
2. **Monitor 1 — the deep check.** Type: *Keyword*. URL:
   `https://api.4truck.us/health`. Keyword: `"status":"ok"` —
   alert **when keyword NOT exists**. Interval: 5 min.
3. **Monitor 2 — the dashboard.** Type: *HTTP(s)*. URL:
   `https://dash.4truck.us`. Interval: 5 min.
4. Alert contacts: your email is on by default. Add Telegram:
   Integrations → Telegram → follow the pairing link (uses
   UptimeRobot's own bot — nothing to install on our side).
5. Test it: `sudo systemctl stop 4truck-bot`, wait ≤8 min for the
   keyword alert ("status" flips to degraded via the silent bot),
   then `sudo systemctl start 4truck-bot` and expect the recovery
   message. This drill proves the whole chain once.

## What this does NOT replace

Internal alerting (thresholds, ingest stalls, error tracebacks)
stays in the bot + operator console — richer, faster, but alive only
while the bot is. External monitoring answers exactly one question
from the outside: "is anyone home?"

## The public status page (owner, ~5 minutes)

The three pages a customer can land on when something is wrong now form
one story, and this is the piece that lives outside our infrastructure:

| Where the failure is | What the customer sees | Who serves it |
|---|---|---|
| Their network or the route to us | `interfaces/dashboard/public/offline.html` | their own browser, from the service worker's cache |
| Cloudflare up, our origin down | `ops/cloudflare/error-5xx.html` | Cloudflare |
| Everything down, or they are on a new device | the status page | UptimeRobot |

The first two say "check the status page", so the link has to answer
when we cannot — which is the whole reason it is somebody else's server.

1. UptimeRobot → **Status Pages** → *Add New Status Page*.
2. Add the `dash.4truck.us` monitor (created in the section above).
3. Name it `4truck`. A custom domain would defeat the point: it must not
   resolve through our DNS or our origin, so keep the
   `stats.uptimerobot.com/…` address.
4. Paste that address into `STATUS_PAGE_URL` in **both** files:
   - `interfaces/dashboard/public/offline.html`
   - `ops/cloudflare/error-5xx.html`

   Until it is set, both files hide every reference to the status page —
   a "Status page" button that opens a vendor's marketing homepage is
   worse than no button, and the offline page's third check would be
   sending a stranded customer to a page we never made.
   `src/test/offlineShell.test.ts` asserts that pairing: empty means
   hidden, set means an `https://` address.

## Uploading the Cloudflare error page (owner, ~2 minutes)

Cloudflare's stock page for an unreachable origin says *"Error 521 Web
server is down"* over a Cloudflare logo, which tells a dispatcher
nothing and reads as a product nobody finished.

1. Cloudflare dashboard → the `4truck.us` zone → **Rules → Custom Pages**.
2. **5xx Errors** → *Custom Pages* → paste the contents of
   `ops/cloudflare/error-5xx.html`.
3. Cloudflare requires the `::CLOUDFLARE_ERROR_500S_BOX::` token to be
   present; it is at the foot of that file, styled down, so the machine
   detail sits under the human sentence rather than over it.

The page is self-contained on purpose: Cloudflare serves it precisely
when our origin is unreachable, so anything it pulled from the origin
would be a hole in the page. The favicon is the one exception and is
allowed to fail.
