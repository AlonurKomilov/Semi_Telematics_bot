# Tours — interactive walkthroughs, the whole system

One paragraph: a tour walks a user through THEIR OWN work on the live
page — each step lights the real control through a dim cutout and
advances when the user performs the real action.  No slides, no demo
mode, no sandbox: steps before the final write live in form state the
page discards anyway, and the one real write is handed back to the
human with its true cost on screen.  Discovery is consent-first: a
small beacon invites, the user knocks, and only then may the system say
what it observed.

This document is the MAP and the LAW — where the pieces live, the
contracts that cross layers, and the decisions with their reasons.  It
lives IN the package, not in root docs, by owner rule: a doc rots where
nobody working sees it, and the seam this file guards changes from this
side (the endpoint, the allowlist) — the same freshness-over-filing
logic that moved tests next to the code they guard.  The root
docs/architecture/README.md indexes it.  The working rules live next to
the code they bind:

  * authoring + engine rules (frontend):
    `interfaces/dashboard/src/components/tour/CLAUDE.md`
  * backend contract: the docstring of `capabilities/tour/__init__.py`

## The four homes

| home | layer | holds |
|---|---|---|
| `interfaces/dashboard/src/components/tour/` | frontend | THE ENGINE — host, beacon, intro, overlay, catalog, guards.  Imports no feature. |
| `interfaces/dashboard/src/features/<x>/tour/` | frontend | that feature's tours — ONE spec per file, `index.ts` collects.  (Singular `tour/`, matching the lane; the PLURAL `features/tours/` is the library page.) |
| `interfaces/dashboard/src/features/tours/` | frontend | the `/tours` library page — browse every tour, per-user verdict chips, unconditional re-run. |
| `capabilities/tour/` | backend | `GET /me/tour-signals` — self-scoped behavioural counts + the `ALLOWED_SIGNALS` allowlist. |

Words: `src/locales/*.json` under `tour.*`, all nine languages,
guard-enforced.  Anchors: `data-tour="<feature>.<name>"` attributes on
real controls — declared, grep-able, never CSS selectors.

## Contracts that cross layers (the reason this file exists)

* **The signals allowlist binds the frontend from the backend.**  A
  tour's `signals` pairs must exist in `capabilities/tour`
  `ALLOWED_SIGNALS`; unknown pairs get a 400, not a zero, and a PYTHON
  guard parses the frontend's `features/*/tour/*.ts` against the list
  (`capabilities/tour/tests/test_signals.py`).  Add a pair backend-first.
* **Self-scope is structural, not policy.**  The endpoint has no
  parameter for another user — it can only ever answer "what have I
  done here", never "what have they".  Nothing is stored.
* **`'tour.state'` is a frozen preference key** (synced): per-user
  verdicts `done | skipped | snoozed`.  done/skip are final; snooze
  re-offers after `SNOOZE_DAYS`.  Renaming the key orphans every
  user's verdicts — the registry's frozen-keys law applies.
* **`/tours?…` → `feature.path?tour=<key>`** is the manual-launch wire:
  consumed by TourHost BEFORE its decide-once guard, so an explicit
  start overrides every verdict by construction.

## Decisions, with the why (do not re-litigate without new facts)

* **"tour", not "spotlight"** — the units were `TourSpec` from the
  first commit and the UI said "tour" in nine languages; one concept,
  one name (the activity-trail precedent).  Renamed at age one day;
  the frozen key was knowingly reset with the owner's explicit call.
* **Client-first** — recorded earn-back from the callouts arc:
  dismissible advice is built client-side; the backend owns only what
  the client cannot honestly know (behavioural signals).
* **Consent gates the observation.**  "I noticed you've added 6 tasks
  one at a time" renders ONLY after the user presses the beacon, only
  with a real signal number, and never faked — who speaks first is
  the entire difference between delight and surveillance.
* **The beacon never escalates.**  No badges, counters, or growing
  glow; ignoring it costs zero.  A beacon that fights for attention is
  the popup it replaced.
* **A tour never presses a trigger it suggested.**  Final steps
  declare `commit: true|false` (build fails undeclared); commit steps
  show the live blast radius and hand over with "Finish tour"; only a
  genuine success (armed click, form closes itself) earns the
  "created" goodbye.  No sandbox mode ever: half-real states — a user
  believing a write happened when it didn't — are the worst outcome,
  and the first live run proved it (a refused submit was congratulated
  by the click-advance engine; `click-gone` fixed it).
* **Counts are CLDR plural families passed numeric**
  (`commit_*`, `intro_observed_*`) — "1 real tasks" shipped and was
  caught by the owner's second live run; ru/uk decline the noun.
* **Adoption retires silently.**  Grouped trail events ARE the bulk
  path; teaching someone what they already do costs attention and
  pays nothing.
* **Repetition is the point of the library.**  One-shot tours don't
  teach; Run again is unconditional.

## Guard inventory (each proven red before trusted)

| guard | enforces |
|---|---|
| `interfaces/dashboard/src/components/tour/tour.test.ts` | anchors exist in source (incl. countFrom), 9-locale field + value parity, plural families, keys namespaced, ≤6 steps, final step declares commit |
| `components/tour/*.test.tsx` | engine behaviour: double-dispatch, anchor unmount recovery, click-gone honesty, beacon occlusion, manual override |
| `capabilities/tour/tests/test_signals.py` | self-scope isolation, allowlist 400s, cross-stack scan of the frontend tour data |
| `routeRegistry.test.ts` | the library page stays reachable from the command palette |

## Extending

Recipe and non-style rules: `interfaces/dashboard/src/components/tour/CLAUDE.md`.  New signal
pair: backend `ALLOWED_SIGNALS` first, then the tour data.  New
surface (bot, miniapp): the verdicts and signals contracts above are
the parts to honour; the engine is dashboard-only by design.
