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
