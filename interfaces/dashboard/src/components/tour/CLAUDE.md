# Tours — interactive walkthroughs (engine + authoring rules)

A tour walks a user through THEIR OWN work on the live page: each step
lights the real control through a dim cutout and advances when the user
performs the real action. No slides, no demo mode — the ending is a
real write the user chose to make. This folder is the ENGINE; it knows
no feature. Everything a feature contributes is data.

## Where things live (one concept, four homes)

| | |
|---|---|
| `components/tour/` | THE ENGINE — host, beacon, intro, overlay, catalog, guards. Never import a feature here. |
| `features/<x>/tour/` | that feature's TOUR DATA — **one spec per file**, `index.ts` collects. A flat `tours.ts` is the old shape; ten tours in one file is a god object. |
| `features/tours/` | the LIBRARY page (`/tours`) — plural, the page listing everyone's tours. Do not confuse with the singular per-feature data folder. |
| `capabilities/tour/` | the BACKEND — `/me/tour-signals`, self-scoped behavioural counts. `ALLOWED_SIGNALS` is the allowlist; a python guard parses the frontend data files against it. |

Words live ONLY in `src/locales/*.json` under `tour.*` — all nine
languages, enforced. Anchors live on the real controls as
`data-tour="<feature>.<name>"` attributes — declared, grep-able, never
CSS selectors (selectors rot silently; the guard fails the build when a
referenced anchor leaves the source).

## The two doors

**Automatic** (the polite one): eligibility → a small pulsing beacon on
the FIRST step's anchor → press → blurred intro (optionally with the
personalized observed line) → Show me → the walk. `adopted()` retires a
tour unseen for people who already do the thing; `skip` is one press
and FINAL; closing the intro merely snoozes (`SNOOZE_DAYS`). The beacon
NEVER escalates — no badges, no counters, no speed-ups. It hides itself
when its anchor is off-screen or occluded.

**Manual** (the unconditional one): the `/tours` library navigates to
the feature page with `?tour=<key>`; TourHost consumes it BEFORE the
decide-once guard and goes straight to the walk, overriding every
verdict. A person asking to re-learn outranks every heuristic.

## Authoring a tour — the recipe

1. `features/<x>/tour/<name>.ts` — one `TourSpec`; add one line to
   `features/<x>/tour/index.ts`; register the array in
   `components/tour/tourCatalog.ts` (first tour of a feature only).
2. `data-tour="<feature>.<name>"` on each step's real control. For a
   container anchor (a chip well), add `advanceWithin: 'button'` — the
   well's padding is not a pick.
3. Copy ×9 locales: `title`, `body`, `step1..N`, `done` — plus the
   families below when used.
4. Keys are `<feature>.<name>`; 1–6 steps (above six it is a course,
   not a shortcut); the FINAL step must declare `commit: true|false` —
   the build fails on an undeclared ending, because the difference
   between teaching and walking a user into 100 real writes is one
   forgotten flag.

## The rules that are not style

- **A tour never presses a trigger it suggested.** A `commit: true`
  step shows the consequence with the LIVE count (`countFrom` names an
  anchor carrying `data-tour-count`) and a "Finish tour" button; the
  write beyond the line is the user's own act. Only a genuine success
  (armed click, form closes itself) earns the "created" goodbye — use
  `advanceOn: 'click-gone'` for any submit that validation can refuse.
- **The observed line is consent-gated and never faked.** "I noticed
  you've added 6 tasks one at a time" is ethical ONLY because the user
  pressed the beacon first — who speaks first is the whole difference.
  `observedCount(ctx)` returns the real signal number or `null`; null
  falls back to the neutral `body`. Never claim what the signals don't
  show.
- **Counts are CLDR plural FAMILIES, passed numeric.** `commit_*` and
  `intro_observed_*` carry `_one/_other` everywhere and `_few/_many`
  where the language declines (ru/uk). A flat string printed
  "1 real tasks" on the very first live run.
- **Signals are self-scoped and allowlisted.** A tour's `signals` pairs
  must exist in `capabilities/tour` `ALLOWED_SIGNALS`; the endpoint
  answers only "what have I done", never "what have they". Endpoint
  down → `relevant()` degrades to page-local evidence, never silence,
  never a thrown offer.
- **Gate on what the server allows.** `ctx.canCreate` must reflect the
  real write permission of the taught flow — a tour must not walk
  anyone into a 403.

## Traps already paid for (do not rediscover)

- A `<label>` click dispatches TWO native clicks — advance is guarded
  once-per-step.
- A resolved anchor can UNMOUNT mid-step (step 1's button may toggle
  the form) — the engine re-waits and recovers; don't fight it.
- Preferences hydrate late: anything reading `tour.state` for a
  decision gates on `useSyncLoaded()` (TourHost and the library both
  do).
- The intro's initial focus belongs on the primary action — Dialog
  focuses the first focusable, which made Skip look emphasized.

Verdicts live in the synced `'tour.state'` preference (frozen key).
Guards: `tour.test.ts` (anchors, locales, families, commit
declaration, step cap) + `capabilities/tour/tests` (allowlist,
self-scope) — every rule here is enforced by one of them, and each was
mutation-tested before it was trusted.
