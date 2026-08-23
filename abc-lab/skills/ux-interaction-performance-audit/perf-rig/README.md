# Perf rig — an isolated board for performance measurement

Never measure against a real account (owner rule).  This rig boots the
real application code on a throwaway docker Postgres, seeds a fake
fleet (12 dispatchers, 69 trucks, ~300 loads, one draft run — the
production board's scale), serves the **production frontend build**
against it, and drives a headless Chromium through the budgets in
[interfaces/dashboard/design.md](../../interfaces/dashboard/design.md)
§ Performance budgets.

## Run it

```bash
python3 abc-lab/skills/ux-interaction-performance-audit/perf-rig/rig_server.py     # scratch PG + seeded API :8010
node abc-lab/skills/ux-interaction-performance-audit/perf-rig/rig_front.js         # dist/ + /api proxy    :8020
node abc-lab/skills/ux-interaction-performance-audit/perf-rig/measure.js           # 3× at 1× CPU, 3× at 4× CPU
```

`measure.js` needs the playwright npm package and a Playwright Chromium
in `~/.cache/ms-playwright` (adjust `EXE` to the revision present).
Reads the login token from `rig_token.txt`, written by the server.

## What its numbers mean — and don't

- **Valid here:** relative A/B of a code change (same box, same noise),
  DOM-node counts, CLS composition, "does the gesture work at all".
- **NOT valid here:** absolute wall-clock budgets.  The dev server also
  runs production; headless Chromium competes with it and reports
  5–10× worse, unstable times.  Absolute budget verdicts come from
  DevTools Live Metrics on a desktop-class machine (see the design.md
  recipe).

## Cleanup — part of the run, not optional

**Owner rule: measurement runs are one-time and disposable.**  Never
store results — no report files, no committed or lingering logs, no
kept scratch builds.  A result's only permanent home is the commit
message of the change it justifies; everything else is regenerated
fresh next time.  A stale number is worse than none: it looks
identical to a fresh one and will be trusted by whoever finds it.

Tear down when done: `docker stop kpi-perf-rig-pg` (self-removing)
and kill the two processes — that leaves zero trace.  The rig never touches the real database, object store, or any
external service (loads are served from an in-process override, the
same way the test suite does it).
