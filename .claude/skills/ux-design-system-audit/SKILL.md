---
name: ux-design-system-audit
description: Design-system compliance audit — fourth member of the ux audit family (psychology reads words/flows, layout reads geometry, sourcing obtains components). Audits whether every rendered value — colour, spacing, size, radius, type, icons, z-index, primitives, naming, per-user state — comes from the project OWN sanctioned system, not call-site improvisation. Step 0 discovers the project design docs, tokens, primitives and guardrails and audits against THOSE — never another project values; uncovered categories become SSOT-GAP findings with proposed rules. Use for "design system audit", "token audit", "consistency check", hardcoded colors/hex, arbitrary spacing, mixed fonts, duplicated primitives, or after any session that built or changed UI; also the routing target the siblings call the design-system audit. On explicit request ("full compliance sweep") run Part V — a grep-driven violation census with per-category counts. Report delivered IN CHAT (no files by default).
---

# UX Audit — design-system compliance

Fourth member of the family: `ux-psychology-audit` reads words and
flows, `ux-layout-composition-audit` reads geometry, this skill reads
**conformity** — does every rendered value come from the project's own
sanctioned system, or was it improvised at the call-site? It is the
routing target the siblings call "the design-system audit".

**Two layers, never confused:**
- **Method (this skill — universal):** the categories to check, the
  invariants that hold in any project, how to discover a project's
  system. Nothing project-specific lives here.
- **Values (the project's own docs and tokens):** WHICH font, WHICH
  scale, WHICH ladder. Discovered fresh in Step 0 for every project,
  never imported from another one. Every concrete value named below is
  an illustration, not a requirement.

Proposed rules always land in the PROJECT's docs (its design.md /
CLAUDE.md), never in this skill — that is what keeps it universal.

> **Boundary (mirror of the siblings' — route, don't double-report):**
> - Meaning collisions (one word, two meanings; a status label shaped
>   like an action) → `ux-psychology-audit` C1/C2.
> - Geometry agreement (zones reading as one, between-vs-within gaps,
>   drop targets, stability, proportion) → `ux-layout-composition-audit`.
> - How to OBTAIN a missing primitive → `ux-component-sourcing`.
> - Tie-breakers (must agree with the layout skill's): an off-ladder or
>   illegal SINGLE value anywhere → **here**, even when it is also too
>   small; ladder-legal values that fail a hit-area floor, invert
>   importance, or disagree between sibling regions → layout skill.
> - Copy-level wisdom that often lives in design docs — "empty states
>   name their constraint", "view controls sit apart from actions" —
>   is C3/C2/S territory; surface it under "Routed", don't score it here.

## Step 0 — Discover the project's system (mandatory)

Never audit against another project's values. Find, in order:
1. **Design docs** — design.md, DESIGN_SYSTEM.md, UI sections of
   CLAUDE.md, Storybook docs, contribution guides.
2. **Token sources** — CSS variables, theme files, tailwind config,
   styled-system/theme objects, design-token JSON.
3. **Primitive inventory** — `components/ui/`-style folders, shared
   shell components, status/colour helper modules.
4. **Guardrails** — lint rules, tests, CI checks that enforce any of
   the above.

Output one short block: which categories D1–D8 the project has rules
for, where each rule lives (a rule can live in code — a tokens file or
a primitives folder IS the system even when no doc describes it), and
which are uncovered. Then:
- Documented rule → audit compliance against the PROJECT's rule.
- System present in code but undocumented (tokens exist, no written
  rule) → audit against that implicit system AND file one `SSOT-GAP`
  proposing the written rule — call-site literals are still violations
  of the implicit system.
- Nothing exists for the category (no doc, no tokens, no primitive) →
  audit against the universal floor below and file the missing rule as
  `SSOT-GAP` with a proposed starting rule (the invariants in D1–D8
  are good seeds).

Missing documentation never blocks the audit — it changes the audit's
output: less compliance-checking, more gap-mapping. On a project with
no design system at all, the most valuable deliverable IS the set of
`SSOT-GAP` proposals — the skeleton of the project's first design doc.

## Documented exceptions & provenance (read before flagging anything)

- **A documented exception is `COMPLIANT`.** A literal or off-system
  value the project's own docs explicitly sanction (an always-dark
  splash background literal, third-party map-pane z-values, numeric
  font sizes inside chart SVGs) is not a violation — cite the
  sanctioning rule in the finding line. Two things still get checked:
  if the docs require a marker (a comment at the site), a missing
  marker IS a finding; and an exception used beyond its documented
  scope is a `VIOLATION`.
- **Trace provenance before flagging foreign vocabulary.** A token,
  class, or variable that looks like a second system may be shipped by
  a dependency (a UI library's own CSS variables or utility layer).
  Find where it comes from — imported stylesheets, package sources —
  before flagging; a library-shipped value is the library's business,
  and the finding, if any, is about the project's wrapper layer, not
  the token's existence.
- **Every `VIOLATION` names the rule it breaks** (otherwise it is an
  `SSOT-GAP` proposal, not a violation); every cleared literal names
  the rule that sanctions it. A violation with no violated rule is an
  opinion.

## Scope modes

Same A/B/C/D as the siblings (session / project / targeted / rendered).
Mode D here means: verify in EVERY theme the project ships (light and
dark at minimum) — a colour fine in one theme and invisible in the
other is a `VIOLATION` only rendering reveals. Mode D never runs
standalone in this audit: compliance is a question of PROVENANCE —
which rule sanctions a value, which source ships a token, which
comment marks an exception — and provenance is invisible in a browser.
A rendered-only report (a screenshot, a live URL, an external auditor
with no repo access) is a set of `[ui]` HYPOTHESES, not findings: run
Step 0 and verify each against the source and docs before any becomes
a `VIOLATION`. Expect refutations — a refuted hypothesis is the system
working, not failing. Never guess about files
you haven't read; mark `NEEDS-CONTEXT` and name what's missing.

## Statuses

| Status | Meaning |
|---|---|
| `COMPLIANT` | Value/pattern comes from the system — name the rule |
| `VIOLATION` | Improvised at the call-site — quote it + exact fix |
| `SSOT-GAP` | No project rule exists for the category — propose one |
| `N/A` | Category genuinely doesn't apply — one line |
| `NEEDS-CONTEXT` | Can't judge without X — name X |

Findings carry the family tags: `Impact: high|med|low · Effort: S|M|L
· Build: existing|compose|new-component|new-dependency`. A missing
primitive that forces screens to re-implement is `SSOT-GAP` +
`Build: new-component` — it hands off to `ux-component-sourcing`.

## The eight categories

The categories are stack-agnostic at the invariant level. Concrete
checks written in web terms (z-index, title attributes, ARIA)
translate into the platform's own terms on non-web stacks — and where
a check has no analogue, it goes `N/A` with a one-line reason rather
than blocking. Hunt patterns are always adapted in Step 0 to the
discovered stack.

### D1. Colour & theming
Invariants: meaning-carrying colour comes from a semantic token layer —
no literal hex, no raw palette classes at the call-site; status and
severity colours flow through ONE status→tone mapping (a single helper
or module), so the same status can never render two hues on two
screens; text colour is DECLARED, never inherited — a surface class
and its matching foreground travel together (the classic bug: a field
with a background and placeholder colour but no value colour is
invisible on the theme nobody tested); non-CSS colour consumers
(charts, maps, canvas) receive tokens via helpers or config constants,
never inline literals; every new surface is verified in all shipped
themes.
Hunt: hex literals in components; raw palette classes carrying
meaning; status colours picked inline instead of via the mapping.

### D2. Space, size, radius, layers
Invariants: spacing sits on the project's step scale — no arbitrary
values for layout; interactive controls, menus/popovers, and dialogs
each pick from a small documented ladder (e.g. three control heights,
three dialog widths) instead of inventing per-screen sizes; corner
radius flows from the theme variable — including JS-DRAWN geometry
(SVG paths, canvas arcs), which must read the live token and re-render
on theme change, because a radius baked into a path number silently
ignores the user's setting; z-index comes from one documented ladder
(content → sticky → panels → floating → above-dialog → blocker), with
third-party pane values as commented exceptions.
Hunt: arbitrary bracket values for layout; hardcoded radius; z-index
outside the ladder; off-ladder control heights and overlay widths.

### D3. Typography
Invariants: one type family; sizes only from the scale, including
SANCTIONED sub-steps for dense UI rather than re-typed arbitrary
pixels; headings picked by ROLE (page / section / card / caps-label)
with one fixed size+weight combo per role — identical on every page,
never improvised by eye; monospace reserved for machine identifiers
(IDs, hashes, code) and never human-readable data; the same logical
column or value styled identically wherever it appears (lift a shared
renderer instead of re-styling per page).
Hunt: arbitrary text sizes; second font imports; improvised heading
combos; mono on names/labels.

### D4. Iconography
Invariants: one icon library — no second set creeping in, no emoji as
UI chrome (emoji in DATA is fine); sizes on the standard steps, paired
to the adjacent text size; icon colour via tokens.
Hunt: off-step icon sizes; inline svg icons duplicating the library;
emoji in controls.

### D5. Primitive reuse & control semantics
Invariants: screens COMPOSE the project's primitives — button, badge,
dialog, empty/error/loading states — never re-implement them; each
recurring CAPABILITY has exactly ONE sanctioned implementation (one
data grid, one menu system with actions-as-data, one tooltip family,
one date-range control, one modal/drawer pair) and a second renderer
for the same concept is a violation even when it looks similar;
"declare, don't implement" — features hand the primitive DATA
(columns, actions, segments) and never rebuild its mechanisms (a local
filter state beside a grid that already filters is in the wrong
place); controls are chosen by MEANING — set-membership → checkbox,
behaviour on/off → switch, behaviour in a toolbar → pressed button —
and one vertical run never mixes identical shapes with different
meanings.
Hunt: raw tables where a grid SSOT exists; hand-rolled menus, tabs,
filter chips beside primitives that provide them; duplicate renderers
for one concept.

### D6. Interaction & accessibility baseline
Invariants: modals and drawers are never hand-rolled — a bare fixed
backdrop has no focus trap, no Escape, no aria-modal, no scroll lock;
scrollable panes a keyboard user must operate are focusable and, when
labelled, named regions, with overscroll containment and
scroll-padding under sticky headers (WCAG 2.1.1 / 2.4.11) — but not
every scrolling list is a region: menus and short pickers stay plain;
native title tooltips are replaced by the project's themed tooltip
system (native ones are unthemed and invisible on touch); icon-only
controls keep an aria-label.
Hunt: hand-rolled fixed-backdrop overlays; title attributes; scroll
panes with no focus story.

### D7. Naming & vocabulary
Invariants: a split or concept that recurs across features uses ONE
vocabulary everywhere — tabs, pickers, badges, group headings; a
per-feature synonym for the same binary is pure learning cost (the
richer domain word may still appear in explanatory copy); one concept
= one CODE name — two identifiers or two renderers for one concept is
the violation even when product copy differs; context-flavoured words
(role names, mode names) stay out of SHARED identifiers and shared
copy — shared types use domain nouns, flavoured names live only in
context-specific artifacts.
Hunt: same concept under two names across features; role/mode words in
shared types.

### D8. State & persistence conventions
Invariants: per-user UI state flows through one preferences
service/registry with typed entries, defaults, and scopes — never raw
storage calls scattered through components; preference keys are FROZEN
(renaming one silently orphans that user's data); anything the backend
acts on is typed data, not a UI preference. No such service in the
project → `SSOT-GAP`.
Hunt: direct localStorage/sessionStorage calls in components.

## Guardrails check

For every violation CLASS found (not each instance), ask: does a guard
exist — a lint rule, a test, a CI check — or does the rule live only
in a doc? Rules without guards decay. Propose the cheapest guard (a
restricted-syntax lint, a grep test that fails the build) under
"Guardrails proposed". A guard that exists but stays warning-level
while new violations land is itself a finding.

## Deep mode — Part V: violation census (on explicit request)

Triggers: "full compliance sweep", "count every violation". Run the
hunt patterns for D1–D8 across the whole scope (adapted in Step 0 to
the project's stack), produce a count table per category per
directory, name the top offender files, then file REPRESENTATIVE
findings — the census is the instance list, one card per instance is
noise.

## Output format (mergeable with the family)

```markdown
# Design-System Compliance Report
- Framework version: 1
- Scope mode: <A/B/C/D, can combine> — <one line: what was reviewed>
- Date: <date> | Auditor session: <short id>
- Step 0 digest: system found at <where> · covered <D-list> · uncovered <D-list>
- Surfaces/dirs audited: <n> | Not yet audited: <list or "none">

## Summary
| Category | Roll-up | Worst finding |
|---|---|---|
| D1 Colour | STATUS mix | <one line> |
| … D2–D8 … | | |

## Findings (by category)
- **[D<n> — STATUS]** `[code|ui|code+ui]` <quoted literal + file:line →
  exact fix in the PROJECT's vocabulary> `Impact: high|med|low · Effort: S|M|L · Build: existing|compose|new-component|new-dependency`

## SSOT gaps & proposed rules (write these into the project's design docs)
- <category — proposed rule>

## Guardrails proposed
- <violation class — cheapest guard>

## Routed to siblings
- <finding + destination (psychology / layout / sourcing)>

## Top actions (highest impact first)
1. ...

## NEEDS-CONTEXT items
- <what and why>
```

Rules: quote the literal and its location — "uses raw colours" without
a quote is an opinion; every fix is phrased in the PROJECT's own
vocabulary (its tokens, its helper names) as discovered in Step 0;
meaning/geometry findings go under "Routed", never scored here; the
`Build` tag lives in the report only — never written into source as a
code comment — and `new-component`/`new-dependency` findings repeat at
the end of Top actions as "→ ux-component-sourcing" items, the
handoff note a later repo session starts from; report
in the user's language on request, statuses and category names stay
English so rollups merge.

## Delivery & aggregation (same as the family)

Full report IN CHAT as markdown — never a report file, never an
invented reports directory; the user copies the markdown out if they
want to keep it, and the repo stays clean. If the user explicitly asks
for a file, write exactly ONE at the path THEY name. Durable outputs
have real homes anyway: proposed rules graduate into the project's
design docs, violations worth tracking become tracker issues — the
report itself stays ephemeral. Rollups merge with P/C/S findings via
the shared Impact · Effort · Build tags, reading reports the user
provides plus the current conversation; the newest report per surface
supersedes older ones; drop findings already fixed; `Build: new-*`
items are verified (and downgraded when over-classified) by
`ux-component-sourcing`.

<!-- SSOT: github.com/AlonurKomilov/skills · 2026-08-21 -->
