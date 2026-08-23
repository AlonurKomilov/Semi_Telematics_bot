---
name: ux-layout-composition-audit
version: 1.0.0
source: https://github.com/AlonurKomilov/skills
description: Layout-composition audit — sibling of ux-psychology-audit, aimed at its blind spot — does the ARRANGEMENT communicate structure before a word is read? Five passes — S1 regions & enclosure, S2 spacing hierarchy (between-group vs within-group air), S3 weight & affordance (source vs destination legible, drop targets visible BEFORE drag), S4 stability (layout holds still), S5 proportion & placement (size tracks importance — Fitts, WCAG 2.5.8; recurring controls hold one position). On explicit request ("region audit", "deep layout audit") switch to Part R — a region-tree census of every container, including hidden-state geometry in conditionals. Use when a surface "looks mixed", components "are not separated", users can't tell where a section ends, for any multi-zone panel (pivot/report/form builders, layer panels, dashboards, drag-and-drop surfaces), after restructuring a layout, or auditing against a mature reference (e.g. MUI) without cargo-culting. Report delivered IN CHAT (no files by default).
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
> | "this gap/radius/size isn't on the scale" · "this control isn't the shared primitive" (e.g. a native checkbox amid styled ones) | `ux-design-system-audit` (discovers and audits against the project's own design docs/tokens) |
> | "this word means two things" · "one object, two faces" · "a region has no heading" · "offered then refused" · "flow starts at 0%" | `ux-psychology-audit` (C1/C2/C3/P) |
> | "these two zones read as one" · "between-gap equals within-gap" · "the empty zone has no drop area" · "source and destination look alike" · "the layout walks under the cursor" · "the page rebuilds on a mode switch" (the geometric fact; the comprehension cost stays with C3) · "too small to hit" · "bigger than its importance" · "this control moved between sibling pages" | **this skill** |
>
> The dividing rule for sizes mirrors the spacing one: the
> design-system audit owns whether a height/width is a legal ladder
> step (`h-8`, `max-w-lg`); this skill owns whether the RESULTING hit
> area is big enough, whether the size matches the element's
> importance, and whether same-class components agree on a step.
> Tie-breaker: an off-ladder size anywhere → `ux-design-system-audit`,
> even when it is also too small; a ladder-legal size that still fails
> the 24px floor, inverts importance, or splits same-class siblings
> across steps → here.
>
> The dividing rule for spacing: the design-system audit owns whether
> a SINGLE value is legal on the scale (is `gap-2` sanctioned); this
> skill owns whether values AGREE across regions and what the spacing
> *says* about grouping. An illegal value anywhere →
> `ux-design-system-audit`. A
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
  `rounded*`, `p-*`/`m-*`, `min-h-*`, `absolute/sticky`. (Examples are
  Tailwind; in other stacks quote whatever carries the geometry — CSS
  rules, inline styles, styled-component props — the same way.)
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

## Step 2 — the five passes

Statuses per pass per surface (same family as the sibling's, so
rollups merge):

| Status | Meaning |
|---|---|
| `CLEAR` | No composition problem — say what carries it |
| `CONFUSION` | A concrete problem — propose the exact structural fix |
| `N/A` | Pass genuinely doesn't apply — one-line reason |
| `DARK-PATTERN-RISK` | Composition used manipulatively — flag it + the honest alternative (S3's gate, extended by S5) |
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
- **Region anatomy (inside the border):** when a region has internal
  parts — a header bar (label, count, fold control), a content list, a
  footer — does each part render as a distinct band (fill step, bottom
  border, or weight), so you can point at where the header ends and
  the content begins WITHOUT reading? A header sharing the exact
  surface, padding and rhythm of the rows it governs reads as the
  first row, not as the bar that owns them (figure–ground; the
  accordion-summary convention: a control that governs a group sits on
  a different plane than the group's members). All region headers on
  the surface share ONE band treatment. A static caption over an
  unenclosed list is a label, not a header bar — the check applies to
  regions whose header IS a control or whose body is enclosed.
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

### S5. Proportion & placement (visual hierarchy · Fitts · WCAG 2.5.8)

Size is a CLAIM of importance and position is a PROMISE of recurrence.
An element bigger than its logical rank lies about the hierarchy; a
control that migrates between sibling surfaces resets muscle memory
every visit.

- **Size tracks logic:** for any pair where one element is logically
  subordinate to the other (an icon to its label, a caption to its
  value, decoration to signal), is the subordinate rendered
  equal-or-smaller ON A LIKE METRIC — text vs text by font-size step,
  icon vs icon by icon step, container vs container by bounding box?
  Mixed icon-and-text pairs are judged against the design system's own
  icon-pairing table (e.g. 16px icon with `text-sm` is a sanctioned
  pairing and CLEAR; only a pairing above its row — a 24px icon in a
  dense table cell — inverts). Name both elements and their rendered
  sizes; every inversion is a finding. Prominence order AMONG a
  surface's ACTIONS — which button out-weighs which — stays S3's
  frequency-and-consequence check; this check owns
  element-to-satellite pairs, so one inversion never lands in both.
- **Same class, one size:** do same-class components — on this surface
  and its siblings — share one size step (all zone headers, all KPI
  cards, dialogs of similar content volume)? A deviation must trace to
  a role difference (S3's territory); "it just came out bigger" fails.
  (Repeated ROW height/rhythm stays S2's check — this one takes
  non-row components: headers, cards, controls, overlays.)
- **Targets are hittable:** compute every pointer target's hit box
  from its padding + content classes; the floor is **24×24 CSS px**
  (WCAG 2.5.8 AA). State the exceptions honestly and completely: an
  undersized target still conforms when (a) SPACING — a 24px-diameter
  circle centered on its bounding box intersects no neighbouring
  target and no circle of another undersized target; (b) INLINE — a
  link inside a sentence or line of text; (c) EQUIVALENT — a ≥24px
  control on the same view does the same thing; (d) user-agent
  default styling; (e) essential presentation. Never claim an AA
  failure for an exempt target — but an exempt target can still be a
  Fitts finding: a micro-target in the top tier of S3's frequency
  ranking takes free, invisible padding whenever it can grow toward
  24px without intersecting a neighbour or reflowing the layout.
  Edge and drag handles rarely qualify for any exception — measure
  them first, they are the usual worst offenders.
- **Container fits content:** a container fails OVERSIZED when the
  next-smaller sanctioned step would hold the same content with no new
  wrap, truncation, or scroll (a wide-editor dialog holding one
  confirm sentence); it fails UNDERSIZED when content truncates,
  clips, or overflow-scrolls at its current step while a larger
  sanctioned step exists. Quote both steps.
- **Expected places:** recurring controls (close/dismiss, the primary
  action, the destructive action) hold ONE position across sibling
  surfaces; two similar dialogs whose primary buttons sit in different
  corners is a finding even when each is fine alone.
- **Honesty (the S3 gate extends here):** size used to steer —
  inflating the option that profits, shrinking the decline — is
  `DARK-PATTERN-RISK`.

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
   A recorded census column is not an audited one: every region whose
   census row shows internal parts (a header entry plus a list) gets
   the S1 region-anatomy check judged explicitly on its card — this
   line exists because the first run RECORDED "headerComposition" for
   a header that failed the anatomy check nobody had yet written.
2. **One card per region**, S1–S5 verdicts each, terse:
   ```
   ### <tree path, e.g. Panel → Values zone>
   - S1 Enclosure — OK | ISSUE: <finding + fix> `Impact · Effort · Build`
   - S2 Spacing — …
   - S3 Weight/affordance — …
   - S4 Stability — …
   - S5 Proportion/placement — …
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
vs a 240–640px rail); copying features that serve their API surface,
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
| Surface | S1 Regions | S2 Spacing | S3 Weight/affordance | S4 Stability | S5 Proportion/placement |
|---|---|---|---|---|---|
| <name> | STATUS | STATUS | STATUS | STATUS | STATUS |

## Findings
### <Surface name>
- **[S<n> <Pass — STATUS>]** `[code|ui|code+ui]` <what the geometry
  does / quoted evidence / the exact structural fix.> `Impact: high|med|low · Effort: S|M|L · Build: existing|compose|new-component|new-dependency`

## Routed to other audits
- <finding that surfaced here but belongs to `ux-design-system-audit`
  or the sibling skill, with its destination — surfacing it is fine,
  double-reporting is not>

## Layout rules proposed (candidates for the design-system SSOT)
- <rule stated generally enough to govern the whole class of surfaces>

## Top actions (highest impact first)
1. ...

## NEEDS-CONTEXT items
- <what couldn't be judged and what's needed>
```

Rules: every `CONFUSION` carries a fix concrete enough to implement
without discussion, plus `Impact · Effort · Build` (Build values as in
the sibling skill: `existing` = components already in the project,
`compose` = assembled from existing primitives, `new-component` = a
new design-system component must be built, `new-dependency` = an
external library is needed — the audit classifies, never picks a
library; `new-component`/`new-dependency` findings hand off to the
`ux-component-sourcing` skill at implementation time, which matters
here more than anywhere: drag-and-drop and virtualized lists are
canonical rung-4 territory; tags self-clean — the newest report for a
surface supersedes its old findings, and `ux-component-sourcing`
downgrades over-classified `new-*` tags after checking the real
inventory); no pass skipped for any
surface (`N/A` with a reason is fine, silence is not); quoted class
strings or screenshot references as evidence; findings that belong to
the other two audits go under "Routed", never duplicated as findings.
The `Build` tag lives in the report only — never written into source
as a code comment; `new-component`/`new-dependency` findings repeat at
the end of Top actions as "→ ux-component-sourcing" items — the
handoff note a later repo session starts from.
If the user asks for their language, write the prose in it but keep
statuses and pass names in English so rollups stay comparable.

## Step — Deliver the report IN CHAT (markdown, never a report file)

Post the full report directly in the chat reply as markdown. Do
**not** write report files and do **not** invent a reports directory —
dated audit files accumulate into stale clutter, and the project
should stay clean; the chat markdown IS the deliverable, the user
copies it out if they want it. If the user explicitly asks for a file,
write exactly ONE at the path THEY name. For long-term tracking,
convert `CONFUSION` and `DARK-PATTERN-RISK` items into tracker issues
and let the report stay ephemeral.

## Aggregation

This skill's reports share the sibling's statuses and `Impact ·
Effort · Build` tags on purpose: a rollup may merge Part P, Part C and
Part S findings into one ranked list — dropping findings fixed or
superseded in the current working tree, collapsing findings that need
the same capability into one sourcing item, and grouping
`Build: new-dependency` items together so external-library decisions
are made once (dependencies overlap and interact). Reports from past
sessions are recoverable only if the user pastes or points at them —
say so in a rollup header rather than implying full coverage.

<!-- SSOT: github.com/AlonurKomilov/skills -->
