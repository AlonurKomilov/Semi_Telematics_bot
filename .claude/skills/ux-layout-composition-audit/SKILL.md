---
name: ux-layout-composition-audit
description: Layout-composition audit — the sibling of ux-psychology-audit, aimed at its blind spot. It audits whether an ARRANGEMENT communicates structure before a single word is read — four passes: S1 regions & enclosure (can the eye count the zones?), S2 spacing hierarchy (does between-group air exceed within-group?), S3 weight & affordance (does visual form declare each region's role — source vs destination, interactive vs static — and is every drag/drop target visible BEFORE the interaction?), S4 stability (does the layout hold still as content changes?). On explicit request ("region audit", "deep layout audit") switch to Part R — a region-tree census that walks every container including hidden-state geometry (empty zones, drag states) found in code conditionals. Includes the sanctioned reference-comparison method (auditing against a mature implementation like MUI without cargo-culting it). Use when a surface "looks mixed", components "are not separated", users can't tell where one section ends, for any multi-zone panel (pivot/report builders, form builders, layer panels, dashboards, drag-and-drop surfaces), after building or restructuring a page/panel layout, or when comparing an in-house component against a reference. Report is delivered IN CHAT (no files by default).
---

# UX Audit — layout composition

Sibling of `ux-psychology-audit`. Same method — scope modes, statuses,
mergeable report — different axis: that skill reads **words and flows**
(names, copy, element meanings, task sequences); this one reads
**geometry** (containers, gaps, weight, motion). A surface can pass
every naming and psychology check and still be unreadable because its
arrangement communicates nothing. This skill exists for exactly that
surface.

> **Boundary (three audits, three questions — route findings, don't
> double-report):**
>
> | The finding sounds like… | It belongs to… |
> |---|---|
> | "this gap/radius/size isn't on the scale" · "this control isn't the shared primitive" (e.g. a native checkbox amid styled ones) | the design-system pass ([design.md](../../../interfaces/dashboard/design.md) + [CLAUDE.md](../../../interfaces/dashboard/CLAUDE.md)) |
> | "this word means two things" · "one object, two faces" · "a region has no heading" · "offered then refused" · "flow starts at 0%" | `ux-psychology-audit` (C1/C2/C3/P) |
> | "these two zones read as one" · "between-gap equals within-gap" · "the empty zone has no drop area" · "source and destination look alike" · "the layout walks under the cursor" · "the page rebuilds on a mode switch" (the geometric fact; the comprehension cost stays with C3) | **this skill** |
>
> The dividing rule for spacing: the design pass owns whether a
> SINGLE value is legal on the scale (is `gap-2` sanctioned); this
> skill owns whether values AGREE across regions and what the spacing
> *says* about grouping. An illegal value anywhere → design pass. A
> disagreement between sibling regions (pool rows `py-1`, zone rows
> `py-1.5` — both legal) → here, even when the fix is a one-token
> swap.

## Scope modes (pick exactly one — same modes as the sibling skill)

- **Mode A — Session** (default when something was built/changed this
  session): only the surfaces built or modified this session
  (`git status` locates them; the working tree is the truth).
- **Mode B — Project** (default when the user says "audit the
  project/platform" or in a fresh session with no prior work): walk
  the product's surfaces, highest-traffic and most layout-complex
  first (multi-zone panels, dashboards, builders before simple
  lists); end with a "Not yet audited" list.
- **Mode C — Targeted:** exactly the files/components the user names.
- **Mode D — Live UI:** whenever screenshots or a browser are
  available, audit the *rendered* arrangement and mark findings
  `[ui]`. Composition is decided by rendered geometry, so Mode D
  evidence outranks intent in the code — but hidden-state geometry
  (empty zones, drag states) exists ONLY in the code, so D never
  replaces the source read; it joins it (`[code+ui]`). Never commit
  state while auditing: abandon drags (Escape or drop outside every
  zone), use test data, no destructive or irreversible actions.

In every mode: never guess about parts you haven't read. If a check
needs another file or a state you can't reach, mark it `NEEDS-CONTEXT`
and name exactly what's missing.

## Evidence discipline (what makes a finding real here)

- **Quote the class strings.** "No enclosure" is an opinion;
  "`border-b border-border last:border-b-0` is the only separation,
  and the last section drops even that" is a finding. Read the actual
  wrappers: `flex`/`grid`, `gap-*`, `space-y-*`, `border*`, `bg-*`,
  `rounded*`, `p-*`/`m-*`, `min-h-*`, `absolute/sticky`.
- **Hunt hidden-state geometry in conditionals.** Every
  `items.length === 0 && …`, ternary, and early return is a shape some
  user will see. Screenshots never show the empty zone, the mid-drag
  state, or the overflow case — the code does.
- **Measure, don't vibe.** "Feels cramped" is banned. "The gap between
  sections is 0 (flex column, no `gap-*`); the gap within a section is
  also 0 (rows carry their own `py-1.5`) — between equals within" is
  the same observation, falsifiable.

## Step 1 — Region inventory

For each surface in scope, list its **regions** — the zones a user
should perceive as distinct groups (a source pool, each destination
zone, a toolbar, a preview, a footer). For each region note: what
encloses it (border? background? nothing but a heading?), what
separates it from its neighbours, and what it renders when **empty**.
This inventory is the audit's table of contents; a region missing from
it is a region the audit silently skipped.

## Step 2 — the four passes

Statuses per pass per surface (same family as the sibling's, so
rollups merge):

| Status | Meaning |
|---|---|
| `CLEAR` | No composition problem — say what carries it |
| `CONFUSION` | A concrete problem — propose the exact structural fix |
| `N/A` | Pass genuinely doesn't apply — one-line reason |
| `DARK-PATTERN-RISK` | Composition used manipulatively — flag it + the honest alternative (S3's gate) |
| `NEEDS-CONTEXT` | Can't judge without X — name X |

### S1. Regions & enclosure (Gestalt: common region)

A shared enclosure — border, background fill, or full-bleed divider —
beats proximity for group membership. A caps label floating above a
list is a *name*, not a *boundary*. Labels alone lose when the lists'
items are interchangeable (assign/drag surfaces) or when S2 also
fails (between-gap ≤ within-gap); a caps label above generously
separated, non-interchangeable sections is the house pattern and is
CLEAR.

- **The count test:** from a static screenshot with nothing hovered,
  can you count the zones and assign every visible item to exactly one,
  without ambiguity? (The row under a "Columns" header must not be
  readable as the last row of "Rows".)
- Does every region that can be a **target** (drop zone, paste target,
  selection scope) keep visible area and its name (a label inside the
  well) while EMPTY — a bounded placeholder of roughly a row's height,
  not a collapsed header? (S1 owns empty-region geometry; S3 owns
  targets that appear only during the interaction.)
- Is the enclosure treatment consistent among same-ROLE regions — one
  grammar for "this is a zone", not a card here and a bare run there?
  (Divergence between OPPOSITE-role regions is S3's mandate, never an
  S1 finding.)
- Typical fixes: give each zone a bounded container (border or tinted
  fill + padding + radius from the system's tokens); a full-bleed
  divider *plus* breathing room where boxes would be too heavy; an
  empty-state well with the zone's name inside it.

### S2. Spacing hierarchy & rhythm (Gestalt: proximity)

Proximity is a statement: closer means more related. When the gap
between groups equals the gap within a group, the statement collapses
and the surface reads as one flat run.

- Is the space **between** regions strictly greater than the space
  **within** them? (Zero-vs-zero fails; a 1px border with no
  accompanying gap between similar-row lists fails.)
- Do repeated rows share one rhythm — same height, same vertical
  padding — across ALL regions? Role difference is carried by weight,
  fill or enclosure (S3), never by row rhythm.
- Do like controls align on one grid — labels start on one x, trailing
  controls (menus, chips) end on one x — so the eye can scan a column
  instead of fixating per row? Reserved slots count: an absent
  checkbox still holds its column.
- Is every indented row a child of the row above it in the data
  model, and every parent–child pair indented — no decorative and no
  missing indents?
- Typical fixes: `gap-*`/`space-y-*` between region containers sized
  above the intra-region rhythm; reserve columns for optional row
  controls; indent children under their parent.

### S3. Weight & affordance (similarity + Fitts + honest prominence)

Visual form must declare role. Two regions that play **opposite roles
in an interaction — take-from vs drop-into, source vs product — must
look different**; sibling regions playing the *same* role must look
the same. (This is the deliberate exception to the sibling skill's
"siblings share one grammar" rule: sameness is only correct when roles
match.)

- Without reading a single label, can you tell: which items are already
  *in* the result vs merely *available*? Which region is the primary
  workspace? What is clickable/draggable vs static?
- Is every drag/drop target visible **with real area BEFORE the drag
  starts**? A target that materializes on dragover is undiscoverable
  and unaimable; a hairline is not a target. (Targets that are merely
  EMPTY are S1's; targets that only EXIST mid-interaction are this
  check's.)
- During a drag, is there **exactly one** position indicator (an
  insertion line OR a shuffling gap — both at once point at two
  different slots), and does feedback name both the zone and the index?
- Is per-item state that **changes the output** (on/off, aggregation
  fn, sort, grain) readable on the row itself — chip, strikethrough,
  badge — not only inside a menu? Two identical-looking rows must not
  produce different results.
- Rank the surface's actions by expected frequency and consequence:
  does the visual-weight order (size, fill, position) match that
  ranking? Any inversion is the finding. (The honesty half is the
  gate below.)
- Typical fixes: distinct treatment for pool vs zones (weight, fill,
  or position); pre-drag drop wells; single insertion indicator;
  inline state chips.
- **Ethics gate (mandatory, same test as the sibling):** composition
  can manipulate — a decline button starved of weight, an exit link
  buried in noise, the paid option enclosed and the free one loose.
  For every prominence choice ask: would we comfortably explain this
  ranking to the user's face? If not: `DARK-PATTERN-RISK`, with the
  honest alternative.

### S4. Stability (object constancy)

A control that moves between two clicks breaks the aim–act loop;
configuration is a rapid sequence, so a walking layout compounds.

- When content grows or moves (a field assigned, a row added, a
  section folded), does exactly **one** designated region absorb the
  change (a `flex-1` scroll area), leaving other regions' headers at
  fixed positions?
- After a mode/state switch, does the layout keep the same regions at
  the same positions with only their contents swapped — or does the
  page rebuild? (The geometric fact is this pass's; the comprehension
  cost of a rebuild belongs to the sibling's C3.)
- Do overlays (menus, drag previews) leave the underlying layout
  untouched — no reflow under a drag, no jump on hover?
- Typical fixes: one elastic region, everything else anchored;
  `min-h` on regions whose content toggles; overlay-based previews
  instead of in-flow mutations.

## Deep mode — Part R: region-tree audit (on explicit request)

Trigger phrases: "region audit", "deep layout audit", "audit every
container". The sibling's Part T walks every *element*; Part R walks
every *container*.

1. **Region census first.** One row per container, from the surface
   root down: wrapper element + quoted classes · enclosure (border /
   fill / divider / none) · gap-before · internal rhythm · **empty
   render** (quote the branch) · interaction states (drag-over,
   selected, disabled) · which region absorbs overflow. Every
   conditional that mounts, hides, or resizes a CONTAINER must map to
   a census row; a container not in the census goes under
   **"Not audited"** at the end.
2. **One card per region**, S1–S4 verdicts each, terse:
   ```
   ### <tree path, e.g. Panel → Values zone>
   - S1 Enclosure — OK | ISSUE: <finding + fix> `Impact · Effort`
   - S2 Spacing — …
   - S3 Weight/affordance — …
   - S4 Stability — …
   ```
3. **Synthesis by the main session:** cross-region consistency matrix
   (which zones share enclosure grammar, which diverge and whether the
   divergence is role-driven), then ONE ranked action list. For large
   surface families the tree may fan out one sub-agent per branch —
   cards only, synthesis never delegated, confirm with the user before
   more than ~3 agents.

## Reference comparison (sanctioned method, with guardrails)

Comparing against a mature implementation of the same component class
(MUI's pivot panel, Excel's PivotTable Fields, Figma's layers panel)
is a legitimate audit move — used one way.

**Valid — mine the reference for states and invariants:**
- Drive it into states you never designed for: zero items, one item, a
  60-character label, minimum width, mid-drag, invalid drop. Every
  state it renders deliberately and yours renders as collapse/overflow
  is a finding — discovered by that component's users, not by taste.
  (No live access to the reference? Mine its source/docs for the same
  states, and mark diffs you can't verify `NEEDS-CONTEXT`.)
- Diff behavior under identical gestures: what commits vs previews,
  what an abandoned drag costs, what carries the insertion index.
- Respect conventions users already hold (pool-then-zones order,
  drag-out-means-remove) — the finding is the relearning cost, not the
  reference's authority.
- **The restatement test — every borrowed finding must survive it:**
  restate the finding as *principle + concrete user cost* without
  naming the reference. "Their empty Values box has height, ours is a
  hairline" → "a drop target with no pre-drag area cannot be aimed
  at." If a note cannot be written without the reference's name in it
  ("MUI puts the count on the right"), it is a preference wearing a
  citation — drop it.

**Cargo-cult — reject on sight:** importing their palette, radius,
chip shape, or gutters into a product with its own token SSOT; copying
their layout topology built for a different canvas (their 600px dialog
vs our 240–640px rail); copying features that serve their API surface,
not a user need you can name; treating their internal inconsistencies
as authority; comparing across interaction budgets (desktop
mouse+keyboard reference for a touch sheet).

## Output format (do not deviate — mergeable with the sibling's reports)

```markdown
# UX Layout-Composition Audit Report
- Framework version: 1
- Scope mode: <A/B/C/D, can combine> — <one line: what was reviewed>
- Date: <date> | Auditor session: <short id>
- Surfaces audited: <n> | Not yet audited: <list or "none">

## Part S summary
| Surface | S1 Regions | S2 Spacing | S3 Weight/affordance | S4 Stability |
|---|---|---|---|---|
| <name> | STATUS | STATUS | STATUS | STATUS |

## Findings
### <Surface name>
- **[S<n> <Pass — STATUS>]** `[code|ui|code+ui]` <what the geometry
  does / quoted evidence / the exact structural fix.> `Impact: high|med|low · Effort: S|M|L`

## Routed to other audits
- <finding that surfaced here but belongs to the design pass or the
  sibling skill, with its destination — surfacing it is fine,
  double-reporting is not>

## Layout rules proposed (candidates for the design-system SSOT)
- <rule stated generally enough to govern the whole class of surfaces>

## Top actions (highest impact first)
1. ...

## NEEDS-CONTEXT items
- <what couldn't be judged and what's needed>
```

Rules: every `CONFUSION` carries a fix concrete enough to implement
without discussion, plus `Impact · Effort`; no pass skipped for any
surface (`N/A` with a reason is fine, silence is not); quoted class
strings or screenshot references as evidence; findings that belong to
the other two audits go under "Routed", never duplicated as findings.
If the user asks for their language, write the prose in it but keep
statuses and pass names in English so rollups stay comparable.

## Step — Deliver the report IN CHAT (no files by default)

Post the full report directly in the chat reply. Do **not** write a
report file. **Exception — explicit request only:** on "save", write
to `docs/ux-audits/<YYYY-MM-DD>-<scope-slug>-layout.md` (LOCAL-ONLY,
gitignored) in addition to the chat reply; `-2` suffix on collision.

## Aggregation

This skill's reports share the sibling's statuses and `Impact ·
Effort` tags on purpose: a rollup may merge Part P, Part C and Part S
findings into one ranked list. Chat-only reports from past sessions
are not recoverable — say so in a rollup header rather than implying
full coverage.
