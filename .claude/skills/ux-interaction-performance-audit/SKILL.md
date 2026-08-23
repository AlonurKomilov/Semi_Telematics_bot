---
name: ux-interaction-performance-audit
version: 1.5.0
source: https://github.com/AlonurKomilov/skills
description: Interaction-performance audit — fifth member of the ux audit family (psychology=words/flows, layout=geometry, design-system=tokens, sourcing=components). Reads TIME — whether the main thread stays responsive mid-gesture — drag, scroll, type, resize — against frame/input budgets. Use when a surface "freezes", "lags", "stutters", "hangs while dragging" (any language — "qotyapti"), when the user asks "is it my PC or the code?", for any drag-and-drop board, large grid, live dashboard or infinite list, or after changing interaction-heavy surfaces. Static pass (hot-path smells, M1–M9) always runs; deep pass (DevTools/Playwright trace) on request or when static can't explain the symptom. Part O headroom pass — nothing broken but the user wants more ("optimize", "make it faster", "10x") — baseline → SLO → Amdahl leverage ladder, plus sync/waterfall coherence. Never assumes the stack, never names a library — Build tags route to ux-component-sourcing. Report IN CHAT (no files by default).
---

# UX Audit — interaction performance

The four siblings audit the still frame. This skill audits **between the
frames**: what the main thread is doing while the user is mid-gesture. A
"freeze" is not a feeling — it is a violation of one of three universal
budgets:

| Budget | Number | Meaning when broken |
|---|---|---|
| Frame | 16.7ms @ 60Hz | missed repeatedly → visible stutter (jank) |
| Long Task | ≥ 50ms | input processing fully blocked for the duration |
| INP | ≥ 200ms | the interaction reads as "frozen" to a human |

Humans start noticing input latency around 100ms. These constants are the
method layer. A project may set stricter targets — that is the values
layer, discovered in Step 0b, never invented here. The frame budget is
`1000 / refresh-rate` — 16.7ms only at 60Hz: in Mode D measure the
actual display (idle rAF interval) and restate the budget (100Hz → 10ms,
120Hz → 8.3ms) before judging frames.

> **Boundary:** wording/flow meaning → `ux-psychology-audit`;
> arrangement/geometry → `ux-layout-composition-audit`; token/value
> compliance → `ux-design-system-audit`; HOW to obtain a needed component
> or library → `ux-component-sourcing` (this skill only tags the need).
> This skill changes *mechanisms*, never *meaning*.

## Scope modes

Same four modes as the siblings — pick exactly one; git doctrine
identical (the working tree is truth, git only locates scope):

- **Mode A — Session scope**: audit the interactions of what was
  built/changed this session.
- **Mode B — Project scope**: sweep interaction-heavy surfaces of the
  whole project. Priority: drag-and-drop surfaces → large
  grids/tables/lists → live-data dashboards → type-ahead forms → the
  rest. List the remainder under "Not yet audited".
- **Mode C — Targeted**: the user names the surface/gesture ("the
  dispatch board drag") — audit exactly that.
- **Mode D — Live/instrumented** (whenever browser access exists:
  `claude --chrome`, Playwright / chrome-devtools MCP, or the user at
  DevTools executing steps this skill dictates): measure the *running*
  product. **The deep pass requires Mode D**; the static pass works in
  any mode from code alone. Mode D combines with A/B/C. Evidence tags
  extend the family notation: `[code]`, `[ui]`, `[trace]`.
  **Mode D safety:** never trigger gestures that write to real data
  (submit / finalize / delete / export / send) — list them in the report
  as deliberately-not-touched; instrumentation must be temporary and
  removed at the end, with the UI restored to its found state; prefer a
  staging run for any write-path gesture. Some benign writes are
  unavoidable (view/preference persistence, telemetry) — enumerate every
  write the audit itself caused, and end in the found state.
  **Writes-heavy surfaces — the seeded-rig pattern:** when every
  interesting gesture writes (a payroll board, an order editor) or the
  owner forbids real accounts entirely, build the staging run yourself:
  a throwaway database container + the project's OWN seed/test path
  filled with fake data at production scale + the production build
  served locally + auth injected as a rig-minted token.  Full gesture
  freedom, zero real-data risk — and the rig doubles as a permanent
  A/B harness for verifying fixes.  Its honesty rule: on a
  shared/loaded machine (a dev box also running production) rig
  numbers are RELATIVE-only — valid for change-vs-baseline and
  structural counts (DOM nodes, CLS composition), never for absolute
  budget verdicts, which need an idle desktop-class machine at the
  throttle the SLO names.
  **Measurements are one-time and disposable:** tear-down is PART of
  the run — no report files, no kept logs, no lingering
  processes/containers/scratch builds.  A result's only permanent
  home is the commit message of the change it justifies (plus the
  in-chat report).  A stale number looks identical to a fresh one and
  WILL be trusted by whoever finds it — always re-measure, never
  re-read.
  **Workspace isolation:** the runner, any rig, and every artifact
  they create (profiles, minted tokens, scratch builds, containers,
  browser installs) live in an ISOLATED disposable workspace — the
  session scratchpad or OS temp — never inside the audited project's
  tree.  The user's project stays clean: nothing to hunt down and
  delete tomorrow.  Hosts and ports are never assumed or hardcoded —
  they come from the profile or the environment, a rig binds
  loopback-only, and a taken port means pick another, never fight for
  it.  If the owner explicitly asks to KEEP a per-project harness, it
  lives in the family's ONE workspace folder at the project root —
  **`abc-lab/skills/<skill-name>/<harness>/`** — never scattered through the
  project's own tree.  `abc-lab/` is the abc-skills family's
  designated lab: every kept tool, rig or script any family skill
  creates goes there, namespaced by its owning skill so ownership is
  readable from the path, each harness self-contained with its runtime
  files git-ignored, and the whole lab carries a one-line README
  saying so.  The contract to the owner: `rm -rf abc-lab` removes
  every trace of the family's tooling from the project, always.
  **Secrets never enter the lab's tracked files:** a kept harness
  contains NO credentials in its code — signing secrets are generated
  per boot, and auth material (tokens, `.env` files, keys) lives only
  in runtime-written files its own `.gitignore` blocks.  The project's
  ROOT `.gitignore` repeats the same patterns lab-wide (logs, `.env*`,
  `*.pem`, `*.key`, `id_rsa*`, `*token*.txt`, `*secret*`,
  `node_modules`, scratch builds) as belt-and-braces, so a future
  harness that forgets its own `.gitignore` still cannot leak.  Before
  a harness's first commit, run the project's secret scanner or
  pre-commit hook if one exists.

In every mode: never guess about code you haven't read — mark
`NEEDS-CONTEXT` and name the exact file or measurement you need.

**No-source context** (live URL via extension, no repo / no source
maps): the static pass cannot read a hot path it cannot see — degrade it
to a bundle-signature scan (virtualization primitives, DnD libs,
CSS-in-JS markers, chart chunks), let the deep pass lead, mark
code-level attribution `NEEDS-CONTEXT`, state plainly that `[code]`
evidence is chunk-level (no file:line), and end the report by requesting
the repo or a source-mapped staging build.

## Step 0 — Grounding (mandatory, never skip)

**0a. Stack discovery.** Read package.json/lockfile and the audited
surface's actual code before saying anything. Establish: framework +
version; state layer and what subscribes to it; DnD mechanism (library?
hand-rolled pointer events? HTML5 DnD?); virtualization present or
absent (which?); animation approach (CSS transition, WAAPI, per-frame
JS, motion lib); styling runtime (build-time CSS vs runtime CSS-in-JS);
what the data layer does during interaction (poll cadence, websocket
pushes, refetch triggers). Inherited rule from `ux-component-sourcing`:
never recommend for or against a library you have not confirmed in the
tree.

**0b. Values discovery.** Read CLAUDE.md / design docs for performance
budgets. If none exist, that is an `SSOT-GAP` finding (family
convention): propose starting budgets — INP < 200ms on core surfaces; no
Long Task > 50ms during a continuous gesture; primary gestures usable at
4× CPU throttle; a per-surface DOM node ceiling; no monotonic
degradation across a session — and write them into the project's values
layer on approval. The skill never hardcodes a project's numbers.

**0c. Symptom capture.** Surface + exact gesture + repro steps; since
when (correlate with recent changes — git as locator only); worsens with
session time or not; which isolation branches the user already answered
(never re-run answered branches).

## Step 1 — Isolation verdict: MACHINE / CODE / ENVIRONMENT

The report must open with this verdict + its evidence. Run only the
unanswered branches:

1. **Extensions off** — incognito / clean profile. Still reproduces →
   environment acquitted. (MutationObserver-based userscripts are a
   classic false culprit on fast-mutating boards — test before blaming,
   acquit when incognito still freezes.)
2. **CPU throttle inversion** — DevTools → Performance → 4× slowdown,
   repeat the gesture. Doctrine: *code that is only usable on a fast
   machine is failed code.* The customer's cheap laptop is the real
   target; a fast dev box masks jank, it never disproves it. This is why
   "is it my PC?" dissolves as a product question.
3. **Scale test** — same gesture at small N vs production N (2 rows vs
   20). Cost growing with N → algorithmic/DOM class (M1/M4/M5), not the
   GPU. This test doubles as a **per-finding control**: before blaming
   any gesture, re-run it at small N — an N-independent cost is a
   browser/window artifact, not the code; record the acquittal so nobody
   re-litigates it.
4. **Browser Task Manager** (Shift+Esc in Chrome) — which process burns
   CPU/GPU; rules out other tabs.
5. **Second engine** (cheap, rarely the answer) — one repro in
   Firefox/Safari to exclude engine quirks.

## Step 2 — Static pass (always runs first)

Read the audited gesture's **hot path**: the event handlers, the
components that re-render during the gesture, the styles of the moving
elements, the data flow that fires meanwhile. A smell counts as a
finding only if it sits on that hot path.

Nine mechanism classes. For each: cues to grep/read, why the budget
breaks, and the fix *shape* (smallest change first — never a rewrite,
never a library name):

### M1. Render scope during gesture
Per-move position stored in framework state → tree-wide re-render per
`pointermove`. Cues: `set*` / `dispatch(` inside move handlers;
unmemoized components inside `.map(` over large collections; fresh
inline objects/callbacks passed to memoized children (defeats memo);
`Context.Provider value={{…}}` above a large tree. Fix shape: per-frame
position via ref + direct `transform` write; commit to state only on
drop; memoize the row/cell layer; stabilize identities.

### M2. Layout thrash (forced reflow)
Layout reads (`getBoundingClientRect`, `offset*`, `scroll*`,
`getComputedStyle`) inside move handlers, interleaved with style
writes → forced synchronous reflow per event. Fix shape: measure once at
gesture start (cache drop-zone rects), batch reads, write inside rAF.

### M3. Layout-property animation
Movement via `top/left/width/height/margin` → layout + paint every
frame. Compositor-only pair: `transform` + `opacity`. Cues:
`transition: top`, keyframes touching layout props, per-frame JS writes
to layout props.

### M4. DOM volume, no windowing
Everything mounted: rows × columns × cells (a dispatch-style grid —
drivers × trucks × 7 days × cards — reaches thousands of nodes fast).
Signature: first paint slow AND every interaction slow AND cost scales
with N (Step 1.3). Fix shape: windowing or `content-visibility: auto` —
but the HOW is a sourcing decision: tag `Build: compose` or
`Build: new-dependency` and route to `ux-component-sourcing`; never name
a virtualization library here.

### M5. Per-render recomputation
Aggregates (sums, rates, totals), sorts, filters recomputed inside
render for every row on every render; type-ahead filtering large arrays
per keystroke. Fix shape: memoized selectors, aggregates precomputed at
data ingest, deferred values for typing.

### M6. Handler & effect hygiene
`pointermove`/`scroll` doing full work per event without rAF coalescing
(events outpace frames); non-passive `wheel`/`touch` listeners blocking
scroll; add-without-remove listeners (degradation signature);
observer/effect feedback loops — ResizeObserver / MutationObserver /
effects writing what they observe. The word "conflict" usually lives
here: two mechanisms driving one element (CSS transition + per-frame JS
writes; two DnD systems both listening; an observer loop re-triggering
renders mid-drag).

### M7. Paint cost
`box-shadow` / `filter: blur` / large gradients repeated across hundreds
of cards; huge repaint regions per move; `will-change` missing where
promotion is needed, or sprayed everywhere (a memory tax).
`[trace]`-verified via paint flashing in the deep pass.

### M8. Main-thread I/O mid-gesture
Heavy synchronous work inside gesture handlers (parsing large payloads,
per-move analytics, verbose logging that forces layout); live data
(websocket/poll) re-rendering the grid **during** a drag — the data
layer fighting the gesture. Fix shape: buffer or pause live updates for
the duration of a gesture; move heavy compute to a worker or idle
callback.

### M9. Degradation over time
Worse the longer the tab lives → leak class: detached nodes,
accumulating listeners, unbounded caches. Confirmed in the deep pass
(heap snapshot pair).

Static finding line:
`file:line · Mx · one-line mechanism (which budget breaks, why) · fix shape · severity · Impact/Effort · Build tag if any · [code]`

## Step 3 — Deep pass (explicit request, or the static pass can't explain the symptom)

Requires Mode D capability — either Claude drives the browser
(Playwright/CDP/chrome extension) or the user executes at DevTools while
Claude dictates and interprets. Protocol in order; stop when the symptom
is explained:

1. **Performance recording** — record → ONE gesture → stop. Read: Long
   Tasks; who owns the main thread during the freeze — Scripting
   (yellow) → bottom-up to the function; Rendering (purple) →
   style-recalc/layout counts + forced-reflow warnings (M2); Painting
   (green) → M7. The screenshot strip is the perceived timeline.
2. **React Profiler** (when React) — same gesture, with "record why each
   component rendered" on. The whole board rendering on one card's
   move = M1 confirmed. Note commit durations against the 16.7ms budget.
3. **Rendering tab** — Paint flashing (repaint area per move), Layer
   borders (compositing sanity), Frame rendering stats (live FPS).
4. **Performance monitor** — live DOM nodes, JS heap, style recalcs/sec;
   watch during the gesture and across minutes (M9).
5. **In-code observers** (temporary, dev-flagged) — `PerformanceObserver`
   on `longtask` + event timing for INP attribution; log duration +
   target. Keeping it as a permanent dev guardrail is a values-layer
   decision — offer it.
6. **Agentic repro** — script the exact gesture (Playwright/CDP), capture
   trace + INP, assert against Step 0b budgets. This converts the audit
   from one-off to repeatable (pre-merge/CI). Method lives here;
   thresholds come from the project.  A bundled profile-driven runner
   ships with this skill at `tools/measure.mjs`: point it at ANY
   project with a small JSON profile (base URL, auth injection,
   per-page ready-signals and gestures, run count, throttle levels) —
   it prints this skill's report tables and writes nothing to disk.
   Scripted gestures are also a free accessibility probe: a
   strict-mode locator failing on DUPLICATE accessible names is a
   real finding — route it to the siblings.
7. **Heap snapshot ×2** (M9 suspected) — before/after N gestures; compare
   detached nodes and listener counts.

Deep findings carry `[trace]` evidence (the trace section or metric), on
top of the `[code]` site when known.

## Part O — headroom pass (performance engineering mode)

**When:** the defect pass comes back all-clear (mostly `WITHIN-BUDGET`)
yet the user wants more — "optimize", "make it faster", "find headroom",
"will it hold at 10× the data" — or on explicit request. Same dimension
(TIME), same instruments (the Step 3 `[trace]` apparatus); the direction
reverses: the defect pass walks *symptom → mechanism*, Part O walks
**baseline → target → gap → leverage**.

**Gate — the premature-optimization gate.** No target, no pass. "Faster"
is not a number. Pull SLOs from the values layer (Step 0b); if absent →
`SSOT-GAP`: propose SLIs (INP per core gesture at 4× throttle; gesture
**settle time** — interaction → last commit/paint — because chunked or
async rendering moves cost to *after* the click and makes INP alone
blind; frame budget at the measured refresh rate; DOM ceiling; load
metrics when load is in scope) with SLO numbers, get approval, write
them down. Only then
measure gaps.

### O1. Baseline
The defect-pass Summary table IS the baseline — reuse it **only after a
validity check**: same build (compare chunk hashes / version), same
display and viewport, same data scale. Any mismatch → declare the old
table stale, re-measure from scratch, and say so in the report; numbers
from a different build poison every share and every multiplier.
Otherwise measure gaps with the Step 3 protocol. Single runs lie: ×3
repeats minimum, report the spread. Note machine class and throttle
state next to every number.

### O2. Critical-path decomposition (Amdahl discipline)
For each gesture at or near its SLO, split total time into measured
segments — input delay / processing / presentation, or fetch / compute /
mount / layout / paint — from the trace. A segment that is share *p* of
the total caps the whole gesture's speedup at 1/(1−p) no matter how hard
it is optimized. **Never propose an optimization without stating its
Amdahl ceiling from a measured share.** Rank candidates by ceiling, not
by fashion. When the trace can't split a share (no source maps; LoAF
samples only frames > 50ms), **measure by intervention**: apply the
cheapest reversible change that isolates the segment (inject a CSS rule,
flip a flag), re-measure ×3, and cross-check the result against the
Amdahl prediction — prediction and measurement agreeing is the strongest
evidence this pass can produce.

### O3. Leverage ladder (descend in order; each rung beats the one below)
- **L1 — Don't do the work.** Remove it from existence:
  default-collapsed groups, route-level code-split, containment so
  offscreen skips layout/paint, precompute/cache at ingest, dedupe
  repeated renders. The fastest work is no work — 10× lives here, never
  in tuning.
- **L2 — Do it later.** Defer off the critical path: lazy mount,
  idle-time prefetch, progressive disclosure, streamed partial results.
- **L3 — Do it elsewhere.** Off the main thread or off the client:
  workers, server-side aggregation, CSS doing what JS was doing.
- **L4 — Do it together.** Batch and parallelize: coalesce K commits
  into one, parallelize dependent fetches (kill waterfalls), request
  batching.
- **L5 — Do it faster.** Micro-tuning: memo granularity, cheaper
  selectors, lighter glyphs/paint. Smallest multipliers — visit last,
  only with L1–L4 exhausted or inapplicable.

Part O finding line:
`surface·gesture · OPPORTUNITY · L-rung · work removed/moved · expected ≤X× (Amdahl, measured share) · Impact/Effort · Build tag if any · [trace]/[code]`

### O4. Temporal-coherence check (fast parts arriving out of sync)
Individually-fast components that assemble unevenly read as freezing
even with a healthy main thread. Four patterns, all `[trace]`-measurable:
- **Request waterfalls** — dependent fetches serialized on the critical
  path (resource timing).
- **Unbatched commits / popcorning** — one logical update landing as K
  staggered commits+paints (commit timeline). Judge it as a trade-off,
  not a defect: chunking can be deliberate INP-protection that moves
  cost into settle time — score it against BOTH metrics, and batch only
  once the underlying work is small enough that INP won't regress.
- **Assembly shift** — late-arriving parts pushing earlier ones
  (layout-shift observation); reserve space with intrinsic sizes /
  skeletons.
- **Unsynchronized transitions** — related elements animating on
  different clocks; drive them from one controller/transition.
Fix shapes change orchestration, never meaning; anything that changes
flow routes to the siblings (scope guard applies).

### O5. Capacity line (boundary with backend)
From the scale tests, state the measured cost-vs-N slope and project the
N at which each SLO breaks ("at ~2× today's rows, expand-all re-exceeds
budget even after L1"). Measure the server share of the critical path
(TTFB / fetch duration): when the server share dominates a gap, the work
item is backend's — name it, route it, do not absorb it into this skill.

## Step 4 — Statuses & severity

| Status | Meaning |
|---|---|
| `FREEZE` | input blocked > 200ms during a core gesture (Long Task chain / INP fail) — ship-blocker tier |
| `JANK` | sustained dropped frames; input lands late but lands |
| `DEGRADATION` | decays with session length — leak class |
| `COST` | measurable waste with no visible symptom on the dev machine yet (e.g., tree-wide re-renders absorbed by a fast CPU) — reported anyway per the Step 1.2 doctrine: "works on my PC" debt |
| `OPPORTUNITY` | Part O finding — nothing broken; a leverage candidate with its L-rung and a measured Amdahl ceiling |
| `WITHIN-BUDGET` | gesture read/measured and meets budgets — cite the evidence |
| `NEEDS-CONTEXT` | can't judge without X — name the file or the measurement |

Dedupe discipline: one mechanism = one finding; list all sites inside
it. Never one finding per file for the same mechanism.

Scope guard: if a candidate fix would change flow or meaning (e.g.,
replacing live drag with click-to-place), flag it and route the UX
question to `ux-psychology-audit` / `ux-layout-composition-audit` — do
not decide it silently inside a perf fix.

## Step 5 — Output format (mergeable with the family)

# Interaction-Performance Audit Report
- Scope mode; surfaces & gestures audited; evidence classes used
  (`[code]` / `[ui]` / `[trace]`)

## Verdict
- MACHINE / CODE / ENVIRONMENT — Step 1 evidence, one line per branch run

## Summary
- Table: gesture × status × dominant mechanism class × Impact/Effort

## Findings (by severity)
- Finding lines in the Step 2/3 format; `Build:` tags quoted verbatim
  for the `ux-component-sourcing` handoff

## Acquitted mechanisms
- Mechanism classes measured clean, each with its evidence (0 rect
  reads, 0 commits, balanced listeners, …). Negative results are
  findings too — record them so later sessions don't re-litigate.

## Headroom (Part O — when run)
- Baseline × SLO × gap table per gesture (spread + throttle state noted)
- Leverage-ladder findings (`OPPORTUNITY` lines), ranked by Amdahl ceiling
- Temporal-coherence results (waterfalls / batching / shift / sync)
- Capacity line: the N where each SLO breaks; server-share items routed
  to backend, not absorbed

## Budgets (values layer)
- Confirmed from project docs, or `SSOT-GAP` + proposed starting budgets
  to write into CLAUDE.md / design docs

## Fix sequence (highest leverage first)
- Typical order: M1 render scope → M2 thrash → M3 transform → M8
  data-vs-gesture → then the M4 windowing decision via
  `ux-component-sourcing` → the rest. Justify deviations.

## Verification checklist
- The exact re-measure that must pass: same gesture, same trace
  protocol, budgets met, at 4× throttle

## Routed to siblings
## Not yet audited (Mode B)
## NEEDS-CONTEXT items

Deliver IN CHAT (markdown, never a report file). If the user explicitly
asks for a file, write exactly ONE at the path they name. For long-term
tracking, convert findings into tracker issues; the report stays
ephemeral.

## Auditor anti-patterns

- Blaming the machine or GPU without Step 1 evidence.
- Naming libraries or proposing rewrites — Step 0a reads the stack;
  `ux-component-sourcing` decides acquisition.
- Running the deep pass by default — static pass first; instrument only
  what reading can't explain.
- Treating every smell as a finding — hot path only.
- Fixing during the audit — the audit ends in a report; patches are a
  separate, user-approved step.
- Measuring only the dev machine at full speed — every `WITHIN-BUDGET`
  claim needs the 4×-throttle qualifier, or an honest note that it
  lacks it.
- Optimizing without a written SLO — "faster" is not a target (Part O
  gate).
- Quoting a speedup multiplier without its measured Amdahl share.
- Starting at L5 micro-tuning while L1–L3 rungs sit unexplored.
- Benchmarking single runs — ×3 minimum, with the spread reported.
- Leaving measurement residue — logs, result files, rig processes or
  containers — after the run; or re-reading any stored number instead
  of re-measuring.

<!-- SSOT: github.com/AlonurKomilov/skills -->
