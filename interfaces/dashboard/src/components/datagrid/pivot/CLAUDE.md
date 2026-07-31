# components/datagrid/pivot — cross-tab reporting

Opt a grid in with the **`pivot`** prop (needs `tableId`). A toolbar
**Pivot** icon opens the fields panel; a **switch inside that panel**
swaps the record list for a matrix: rows × columns × aggregated values.

## Opening and switching on are two different acts

This follows MUI. The toolbar icon (lucide `Table2` — a grid with an
emphasized header row AND first column, i.e. the shape of a cross-tab)
only opens the panel; the switch at the panel's top pivots the grid.

It used to pivot on the *first click*, which replaced the row list
before the operator had said what they wanted summarised, and then
needed a second "Fields" button to reach the pickers. One control now
does both jobs in the right order. Two things keep that honest:

- the toolbar button paints **active** while pivoted, so a closed panel
  never hides the fact that you're looking at a report rather than a
  list;
- the panel is a **sibling of whichever body is showing**, not a child
  of the pivot branch — it has to render while the grid is still a row
  list, because that is where you switch pivoting on.

Flipping the switch on still seeds a STARTER model (first dimension ×
first measure) so the report isn't born empty — but now it appears
right beside the pickers that shaped it, reading as a suggestion to
refine rather than a decision made for you.

**The toolbar does not change shape when you pivot.** Manage-columns is
superseded while pivoting (columns come from the model), but it stays
put, disabled with the reason. Hiding it re-flowed every icon to its
right the instant you toggled, so the control under the cursor stopped
being the one you were aiming at — which costs more than a greyed
button explains. This supersedes the earlier "hidden rather than
greyed" note for the pivot case; the rule still holds for controls that
appear and disappear with the DATA, not with a mode the user just
chose.

**The panel is drag-resizable** from its left edge (240–640px), stored
in `pivot.panelWidth` — device-scoped like `assistant.panelWidth`,
because how you split panel-vs-report is a judgement about the screen
in front of you, not about you. The panel takes its width FROM the
table, and that tension is genuine: a deep field list wants width, the
matrix behind it wants it back. Under `fillHeight` the panel stretches
to the grid's height instead of capping at 32rem, so it can't float
short of a much taller card.

```
pivot.ts         the pure transform (+ pivot.test.ts) — no React
PivotView.tsx    the read-only matrix renderer
PivotPanel.tsx   Rows / Columns / Values pickers
drill.ts         drill-down: which rows are behind a cell (pure)
DrillDialog.tsx  drill-down: how they're shown (+ owns its open state)
derived.ts       generated Year / Quarter / Month date grains
```

Sub-features get a **pure file + a React file**, named for the feature
(`drill.ts` / `DrillDialog.tsx`), not scattered through the two big
ones. Nothing outside this folder imports any of them.

## Why it's a separate renderer, not a DataGrid mode

**A pivoted grid is a REPORT, not a record list.** Pinning, resize,
per-column ⋮ menus, row selection and drag-reorder are all meaningless on
synthesized aggregate rows. Routing pivot output back through DataGrid's
4k-line renderer would mean flag-disabling most of that file from inside
it — permanent conditional soup. `DataGrid.tsx` therefore holds only a
toggle and ONE branch.

The multi-level header is a plain `rowSpan`/`colSpan` `<thead>`. It does
**not** touch the grid's `groupRuns` bracket row, which is entangled with
drag-reorder and pin offsets and already works.

## Which grids get `pivot` — three tests, all three required

Coverage is not "every grid eventually". A grid earns the toggle only
when all three hold; failing any one makes the pivot confidently wrong
or merely a slower version of row-grouping:

1. **Rows are EVENTS, not entities.** One row per load / work order /
   fuel purchase / inspection pivots. One row per vehicle / driver /
   part / service task does not — those grids are already one row per
   thing, so a pivot has nothing left to collapse.
2. **At least one ADDITIVE measure.** Sums of sums are true; averages
   of averages are not. A column that is itself a rollup (a driver's
   period `score`, a `$/mile`) must not become a pivot value — the
   matrix would print a confident number nobody can reproduce.
3. **Two dimensions, or one plus a date.** With a single dimension and
   no time axis the report degenerates into "group by X with totals",
   which the grid already does via row-grouping + footer aggregation.

Worked exclusions (checked, deliberately NOT wired): **Fuel summary**
and **Cost Per Mile** (already per-vehicle aggregates — test 1+2),
**Part Detail's** by-vehicle/by-vendor rollups (test 1), **Parts**
(measures live on the catalog grid, the `category` dimension on the
public one — nothing but Assembly is left, test 3), **Scorecards** (no
date column at all and `score` is a per-driver average — test 2+3),
**Vehicles** / **Service Tasks** (registries — test 1).

## Opting a column in

- **Dimensions** (Rows / Columns pickers): `pivotable: true`
- **Measures** (Values picker): the existing `aggregable: true`
- **Date columns need nothing.** Any `pivotable` column that already
  declares `aggType: 'date'` or `filterMode: 'date-range'` is REPLACED in
  the pickers by generated **Year / Quarter / Month** dimensions
  (`derived.ts`), bucketed in the account timezone. The raw date is
  dropped as a dimension because grouping by an exact timestamp yields one
  column per row.
- Only override when the generated grains are wrong: a `pivotValue` on a
  date column wins and suppresses generation. Non-date buckets still use
  `pivotValue` + `pivotLabel` (`2026-01` → `Jan 2026`).
- Accessor precedence: `pivotValue` → `filterValue` → the raw cell.

## Correctness rules the tests pin

- **`count` reports the CELL population**, not the row-bucket total —
  otherwise one number repeats across every column.
- **An empty intersection is `null`, rendered as a dash — never `0`.**
  Zero is a measured claim; "no rows here" is not.
- **Missing numerics are excluded, not folded in as 0.** The transform
  REUSES `../aggregation.ts` rather than re-implementing reduction, so
  the null→0 and null→1970 bugs solved there can't come back.
- Buckets sort, so `YYYY-MM` months read chronologically for free.
- A saved model naming columns the grid no longer has is pruned
  (`prunePivotModel`) — the same staleness rule saved tabs apply.

## The matrix takes its natural width

The table is **`min-w-full`, never `w-full`**. Pinned to the container
width it has no choice but to compress — 60 driver columns squeezed to a
few characters each, headers wrapped to three lines, and nothing to
scroll because nothing overflowed. Allowed its natural width it
overflows and the wrapper's `overflow-x-auto` finally has a job, which
matters most exactly when the fields panel narrows the grid. Header
cells are `whitespace-nowrap` for the same reason: a wrapped header is a
column that gave up its width instead of claiming it.

This is also what makes the two sticky edges earn their keep — the
row-label column and the Total column only mean anything once there IS
horizontal scroll.

## Both edges of the matrix are pinned

`<thead>` and `<tfoot>` are `sticky` under `fillHeight`, exactly as list
mode pins its own. A three-level header that scrolls away leaves every
figure without a column identity — with 60 columns there is nothing to
guess from — and a grand total reachable only at the end of a scroll is
not a summary. `z-30` on both: it clears every sticky cell in the body
(max `z-20`), so the frozen Total column cannot paint over the header it
belongs under.

## Every setting is on a FIELD's ⋮ — no zone has a menu

`fieldSettings(axis)` adds the zone's setting to **every field in that
zone**, between the move block and the axis list: it changes how the
zone's column is DRAWN, not where the field lives.

| Zone | Item on each field's ⋮ |
|---|---|
| ROWS | Pin row labels |
| COLUMNS | Hide columns with no values |
| VALUES | Pin Total column *(counted when >1 measure)* |

This is where list mode keeps Pin and Hide — on the **column** menu —
and it is the owner's decision, reached after two worse drafts: inline
controls stacked above the field rows (five near-identical checkboxes in
one run, one governing the zone and four governing membership), then a
settings ⋮ on each zone's header band. With nothing left to host, that
band went back to being the fold control alone, and **no zone renders a
⋮ at all**. A test asserts that.

⚠️ **Every label names the OUTPUT, never the field you opened** — "Pin
row labels", not "Pin Company" — and each item appears on every field of
its zone showing the **same** state. Whichever you open, it is the same
setting. The reason is the same in all three zones: they govern what the
fields *jointly* produce.

- **ROWS** — the renderer draws ONE merged label column for every row
  field (`rowFieldLabel` joins them with `" / "`, and the body is a tree
  inside that single cell), so this freezes Company AND Customer
  together.
- **COLUMNS** — `colPaths` is built from all the column fields, so the
  buckets pruned are the combinations they jointly produce.
- **VALUES** — the Total column is GENERATED from the measures, **one per
  measure**, so pinning from Rate's menu freezes every Total column. The
  label counts them ("Pin 2 Total columns"); a hard-coded singular
  under-described it the moment a second measure was assigned.

Naming the field instead would promise a per-field effect the matrix
cannot perform.

**Per-field pinning would need a flat/tabular row layout** — row fields
rendered as separate columns (Excel's "Tabular" vs "Compact") instead of
a tree in one. That is a real feature touching the header build, the body
renderer, the collapse model, the sizer row and the windowing
arithmetic — not a menu change. Until then, one column means one pin.

**The word is list mode's own.** Freezing a column against an edge is ONE
concept, so it gets one name, sharing the verb with the column ⋮'s Pin
submenu (Pin to Left / Pin to Right). An earlier draft said "Keep row
labels in view" — a second name for something already named, which makes
a returning user relearn what they know.

⚠️ **"Hide columns with no values" is deliberately NOT shortened to list
mode's "Hide".** That hides ONE column the operator picked; this prunes
every bucket that came out empty. Same word, different act — sharing it
would be a false friend, which is the opposite of the win above.
The panel had drifted from that — the settings were inline controls
stacked directly above the field rows, so COLUMNS showed **five
identical checkboxes in one vertical run**, one governing the zone and
four governing which fields contribute. Same shape, same x, two
unrelated meanings. (Owner-reported, twice.)

**Why the zone and not a field.** ROWS renders ONE merged label column
(`rowFieldLabel` is the row fields joined with `" / "`, and the body is a
tree inside that single cell). So a pin on Company would silently govern
Customer, and it would hop to a different field's menu the moment the
zone was reordered — a setting that relocates for a reason unrelated to
itself can't be found twice. The Total column has the mirror problem: it
is generated FROM Values, so it is not a field at all.

Consequences to keep:

- The header band is a **row, not a button** — it carries a fold control
  and a menu button, and a `<button>` may not contain another button.
  The fold target keeps `flex-1` so the band still reads as one click
  surface.
- **A checkable item fills its icon slot in BOTH states** (`check()`).
  `MenuActionList` renders `{icon}{label}` with no reserved column, so an
  unchecked item sits left of a checked one and the label visibly jumps
  sideways as you toggle it.
- A zone with no fields, and VALUES with no column dimension, return `[]`
  — no ⋮ at all rather than a menu whose only item is a dead end.

The remaining shape rule, now dashboard-wide in
[../../../CLAUDE.md](../../../../CLAUDE.md): **checkbox = "is this item in
the set?", switch = "is this behaviour on?"**, and a behaviour that must
live in a toolbar or header is a **pressed icon-button** (which is what
drilling is).

## Both frozen edges are opt-in, and the controls are FIELD-level

`pinRowLabels` and `pinTotals` default **OFF** (owner's call, and MUI's
behaviour — a frozen column costs real width on every report) and are
toggled from a FIELD's ⋮ (see above). Turn them on when a wide matrix
starts scrolling the identity out of sight.

Each is ONE setting reachable from several menus — the row fields share a
merged label column, and the Total columns are generated from the
measures — so the labels name the column rather than the field. The Total
pin is withheld entirely when there is no column dimension to total
across.

Unpinned, those cells become **ordinary**: no position, no z-index, no
opaque fill, no seam, and no zebra overlay. A cell that isn't frozen has
nothing to occlude, and keeping `bg-card` would only hide the row's own
stripe. `data-pin` goes with them, or the horizontal scrollbar would keep
reserving an inset for an edge that now scrolls.

The prune writes the flag EXPLICITLY (`?? false`) rather than leaving it
undefined, so a future change of default can't silently move reports that
already exist. Note the one-off cost of the current default: reports
saved while pinning was on have no explicit value, so they pick up the
new default and unfreeze once.

**These are the only pin controls in pivot mode.** The record list's
per-column ⋮ (Sort / Filter / Pin / Group / Hide) does not exist here —
the matrix is a separate read-only renderer with no per-column menus — so
the panel is the only place the setting can live.

## Never append `relative` to a sticky cell

`sticky` and `relative` are the SAME tailwind-merge group (`position`), so
`cn(stickyCol, 'relative …')` keeps the LAST one and the column silently
**stops freezing**. That shipped: a `relative` was added to host the zebra
overlay, and both frozen body columns thawed. It is nearly invisible —
jsdom has no layout, and in a browser the header corner and the totals
label keep their own `sticky` and look right, so what you see is a pinned
header floating over unrelated data rather than an obviously broken
column.

`position: sticky` is itself a positioned value, so it ALREADY forms the
containing block an `absolute inset-0` overlay resolves against —
`relative` was never needed. `PivotView.windowing.test.tsx` asserts the
merged class string still contains `sticky` and not `relative`, because
the merged string is the only part of this that is checkable without a
layout engine.

## A pinned cell must be fully OPAQUE

The zebra stripe is `bg-muted/30` — 30% alpha. Applied directly to a
sticky cell it wins the cascade over that cell's `bg-card`, leaving the
frozen column **70% transparent**: the scrolled columns underneath bleed
through and the text visibly overlaps (`4,72RMR (65)`,
`$2,550$2,550.00`). Sticky positioning lifts a cell above its siblings,
but only its own opaque background occludes them — the row's background
paints *behind* every cell, so it can't help.

So sticky cells keep `bg-card` and take the stripe as an **inset
overlay** span, hidden on hover so the row's hover fill isn't doubled.
That also matches the body's stripe exactly: same alpha over the same
card colour.

## `hideEmptyColumns` — prune buckets that hold nothing

Opt-in on the model, toggled from the COLUMNS section of the panel (it
governs column buckets, so it lives with them, and it's only offered when
there IS a column dimension). On a wide report most intersections are
empty — a driver appears in a handful of companies, not all of them — so
a 61-column matrix can be ~90% dashes. Pruning is a **legibility** win
first; the render saving is a bonus.

Three rules the tests pin:

- **Rows are not the test, a finite MEASURE is.** A bucket can have rows
  and still be all dashes because the measure is missing for them.
- **`count` keeps its bucket.** It reports the population, so a column
  with rows shows a real number — pruning it would delete an answer.
- **Never prune to nothing.** An all-empty report would collapse to bare
  row labels with no explanation, which reads as broken.

`PivotResult.hiddenColumns` is reported up to the footer band ("12 empty
columns hidden") because a matrix quietly missing columns is worse than
one showing empty ones.

## Row windowing

Under `fill` the matrix renders a WINDOW of rows — roughly the viewport
plus 15 rows of overscan each side — with spacer `<tr>`s carrying the
height of everything not rendered. ~2,000 cells in the DOM instead of
~22,000.

- **The subscription is QUANTISED.** It re-windows once per BUCKET of 10
  rows scrolled, not per frame. This is the sanctioned exception to
  "nothing outside a scrollbar subscribes to scroll" (see
  [../CLAUDE.md](../CLAUDE.md)): it watches a coarse bucket, not the
  position, so a scroll costs a handful of ~2,000-cell renders rather
  than 60 renders of 22,000.
- **Zebra keys on the ABSOLUTE index** (`win.from + i`). Keyed on the
  slice index the stripes strobe as you scroll.
- **Rows must be UNIFORM height** — the offsets are `from × rowH`. The
  fold button used to make parent rows ~6px taller than leaves, which
  would drift the further you scrolled; the row content now carries
  `min-h-6` and the button is exactly 24×24 (the WCAG floor, and on the
  4px scale).
- **`from` is clamped against the list's own end.** Scroll deep into 360
  rows, then collapse every group to 4: the bucket is still ~30, so an
  unclamped start slices past the end and you get a tall spacer with no
  rows behind it.
- **It self-bootstraps.** `rowH` is 0 on first paint, so windowing is
  off, all rows render, one is measured, and the window engages. It also
  stays off for any list that fits — no spacers, no chance to be wrong.
- Gated on `fill`: a non-`fill` matrix has no owned scroller.

The arithmetic (spacers + slice always accounting for every row) is
pinned by tests in `pivot.test.ts` rather than jsdom, which has no
layout.

## Column widths come from the REPORT, not from the window

Auto table layout sizes a column from the cells that are in the DOM. Once
rows are windowed that is whichever rows you happen to be scrolled to, so
widths would snap every time a wider figure scrolled in.

The fix is a **sizer row**, mounted only while windowing: one zero-height
`aria-hidden` row carrying, per column, the widest candidate the whole
report holds — through the *same* `cellPad` classes and the *same*
`renderCell`. The browser's own sizing then settles on a width that is a
function of the data. No measurement pass, no pinned pixel values,
nothing to drift out of sync with the real cells.

- `visibility: hidden` preserves layout, so the width still counts;
  `py-0 h-0` plus a clipping span removes every visual trace.
- It carries **no `data-prow`**, so the row-height measurement can't pick
  it up as a real row.
- The candidate is the greatest **magnitude** across every body row *and*
  the grand total — more digits means a wider string under one formatter.
  An estimate with a guarantee: never narrower than anything that can
  appear in the column.
- **Why the grand total isn't enough on its own:** for `sum`/`count` the
  always-mounted `<tfoot>` already dominates (a sum is at least as wide as
  any addend). For **`min`** it is the opposite — the grand min is the
  smallest number, so the narrowest string, while a body cell can be far
  wider. That case is what the body pass exists for, and a test pins it.
- The sizer must never drift from `renderCell`/`cellPad`. Format the
  candidate any other way and the pinned width stops matching the cells.

## Cost: what is still not virtualised

Rows are windowed (above), so the DOM holds ~2,000 cells. COLUMNS are
not — all 61 render for every windowed row. Two things this makes
non-negotiable:

- **Never hand it a fresh `rows` array.** Built inline in JSX the prop
  changed identity every render, so `useMemo` never hit and the whole
  cross-tab was rebuilt for any unrelated state change. `DataGrid`
  memoises `pivotSourceRows`.
- **Nothing may re-render it at scroll rate** — see the scrollbar note in
  [../CLAUDE.md](../CLAUDE.md).

Initial render is still heavy by construction. Virtualising a table with
two sticky edges, nested header levels and a slack-absorbing row is a
real piece of work; until then the honest mitigation is narrowing the
column dimension.

## The totals row sits at the BOTTOM, not under the last row

`sticky bottom-0` alone only pins a totals row once the content
OVERFLOWS — with four rows there is nothing to stick past, so the total
landed directly beneath the last one and floated mid-card above a large
blank area. A totals row belongs at the foot of the surface whether
there are four rows or four hundred.

The fix is a **slack-absorbing row**: an `aria-hidden` `<tr>` with
`height: 100%` after the data rows, plus `h-full` on the table so the
percentage resolves. Tables hand surplus height to such a row, which
pushes the footer to the bottom edge; the moment the rows do overflow it
collapses to nothing and the sticky behaviour takes over. Both renderers
do this — the record list's aggregation footer had the identical defect.

## Hand-rolled cells must restate the primitives' padding

`DENSITY_PADDING` is **vertical only** (`py-1` / `py-3` / `py-5`). List
mode gets horizontal padding from the primitives (`TableCell` is `p-2`,
`TableHead` is `px-2`); this view hand-rolls `<td>`/`<th>` and inherited
none of it, so adjacent columns' figures touched — `—$47,200.00`,
`$28,$1,530,862.60`. Every matrix cell uses `cellPad` (`padding` +
`px-2`), never `padding` alone.

**And a rule taught in the header is kept in the body.** The group
header levels draw `border-l` at each boundary; the leaf level and the
body cells now do too (`border-border` for chrome, `border-border/50`
for data). Teaching a separator in one band and dropping it in the next
makes the header a promise the data breaks — 60 identical `RATE sum`
labels over an unruled number field can't be traced to their column.

## The card keeps one skeleton across both modes

Pagination is genuinely meaningless on a pivot, so the footer bar goes —
but the band it occupied must not just become dead space. Under
`fillHeight` PivotView pins its two edges and scrolls only the matrix
between them:

- there is **no caption band**. It used to state the scope and explain
  why paging had gone, but the tab strip already says "All rows 500" and
  the footer says "Total rows: 4" — so it was a third telling of the
  same fact, costing a row of height on every report.
- the **panel is a PEER COLUMN of the whole grid** — toolbar, body and
  footer all sit to its left — not a box inside the body region. Nested
  in the body it began below the toolbar and stopped above the footer,
  which read as something the table contained. MUI stands it alongside,
  which is what it actually is: a second surface, not part of the table.
- the **row count** reports the REPORT's size (`bodyRows.length`), not
  the lines currently on screen. Counting visible rows made a "total"
  that shrank every time a group was collapsed — describing the
  viewport, not the report.
- the **row count** is rendered by `DataGrid`, in the pagination bar's
  own slot and with its exact shell (`p-3 bg-muted border-t`, `text-xs`,
  filter hint left / figure right). PivotView reports the number up
  (`onRowCount`) rather than drawing its own band: drawn locally it sat
  inside the matrix COLUMN — stopping at the fields panel instead of
  spanning the card — at a smaller size with no background, so the
  card's bottom edge visibly changed shape on a mode switch. One shell,
  two contents.

Both used to sit INSIDE the vertical scroller with the table, so at most
scroll positions you could see neither, and the card ended in an empty
strip.

## What stays live, what goes

The rule: **scope stays, column machinery goes.**

| Live in pivot mode | Hidden (superseded) |
|---|---|
| segments / saved tabs, filter chips, global search — they narrow the pivot's INPUT | manage-columns, pagination |

Hidden rather than greyed: a disabled control the operator can never
satisfy is worse than one that isn't there. The stored column layout
prefs are untouched and return when pivot is switched off.

PivotView is fed the **same post-filter/search rows the footer
aggregation reduces**, so the two can never disagree.

## Sorting

Click a measure header to order rows BY that measure: desc (biggest
first — the question people actually ask) → asc → back to label order.
The choice lives in the model, so it persists with the rest of the
report.

⚠️ **`sort` must survive every model rebuild.** It shipped BROKEN for
exactly this reason: `prunePivotModel` and DataGrid's model memo both
reconstructed the model field-by-field and neither copied `sort`, so
every header click wrote a sort the next render threw away — no
reorder, no caret, a dead control with a live tooltip. The stored
preference type didn't even have the field. Every test passed
throughout, because they all call `pivot()` directly and never touch
the persistence path. **Any new PivotModel field must be added to
`prunePivotModel`, DataGrid's `pivotModel` memo, and the registry's
stored type together** — `pivot.test.ts` has round-trip tests that go
red if `sort` stops surviving.

Two rules the tests pin: sorting happens WITHIN each parent, never
across the flat list (that would tear children out of their group); and
a row with NO value in the sorted column sinks to the bottom in BOTH
directions — absent is not "smaller than every number". A sort naming a
leaf that no longer exists falls back to label order rather than
discarding the report.

## The Total column

With a column dimension the matrix gains a **Total column**, pinned
right, mirroring the Total row — a 2-D pivot could otherwise show every
driver's contribution to a company but never the company's own figure,
which is usually the number the reader came for.

Both pinned edges (row-label left, Total right) carry a seam — a 1px
inset line + the grid's `--pin-shadow-*` token, via box-shadow because
these tables are border-collapse and borders don't travel with sticky
cells. Without it, mid-scroll columns slid under the pinned ones with
headers fusing into one word (layout-audit S1 catch, owner-reported).

Its values are **re-aggregated from the source rows**, never summed
across the cells: adding per-column averages is arithmetic nonsense and
`count` would multiply. Same rule as the Total row. Without a column
dimension there is no Total column — the leaf columns already are the
per-row totals.

## Drill-down

**Opt-in, default OFF** — "Open the rows behind a figure". Same default
as the two pins: a report starts as the report you asked for.

**The control is in the panel HEADER, beside the pivot switch — not in a
zone.** It began in VALUES, when only value cells drilled; once the Total
column and the footer became drillable it governed the whole matrix, and
a report-wide behaviour parked inside one zone reads as if it applied
only there. It is a **pressed icon-button** (`ListTree`, `aria-pressed`,
filled when on) rather than a switch — owner's call, and it matches the
toolbar's own active-Pivot convention. Disabled with a reason while the
grid isn't pivoted: there is no report to drill into yet.

The label is written in the imperative, describing what it does to the
REPORT. An earlier draft read "Click a figure to see its rows", which
instructed the USER instead. "Drill-down" stays out of the UI entirely —
our word, not the reader's.

⚠️ **Default-OFF means the panel's help text must not assert the
behaviour.** The Pivot InfoTip used to end "Click any figure to see the
rows behind it", which became false for every new report the moment the
default flipped — the user clicked, nothing happened, and the only help
on the surface was what misled them. It now points at the switch, which
also carries the discoverability a default-off feature otherwise loses.

### Every figure drills — no exceptions

Leaf cells, the Total column, the grand-total row and the bottom-right
corner. The rule has to be learnable in one sentence, and "figures open
their rows, except that one column" is not it — the Total column sits
inline with the body rows and looks exactly like them.

This falls out of `pivotCellRows` rather than needing special cases: an
empty `colPath` means "every column", which IS the Total column's
definition, and an empty `rowPath` means "every row", which is the
footer's. The four cell kinds differ only in which paths they pass.

The `Total` LABEL in the footer stays uncoloured and unclickable — it
names the band, it isn't a figure.

Switched OFF, a figure is a plain text node. Not a disabled button, not
a button without a handler — **no button at all**, so a dense matrix
doesn't carry thousands of focus stops for a feature nobody turned on.
The `<td>` is `p-0` and expects a padded child, so the plain span must
restate `cellPad` or the figures fuse with the next column.

⚠️ The label has to SAY what the feature does, because default-off means
nobody meets it by accident. "Drill-down" is our word, not the reader's.

Switched ON, every non-empty cell is a button. Rows are recomputed on
demand (`pivotCellRows`) — caching them per cell would hold an array for
every cell in the matrix to answer a question asked about one.

Clicking a COLLAPSED PARENT drills its whole subtree, because the
parent's number IS the sum of its descendants — a drill-down that showed
fewer rows than the number accounts for would be lying. A test asserts
the drilled rows re-aggregate to the figure on screen.

### The dialog names the whole ancestry

`DrillTarget` carries `rowPath` (query keys) AND `rowLabels` (display
strings) as separate fields, plus `colPath` and `colLabel`. That is not
redundancy: a month bucket's key is `2026-01` while its header reads
`Jan 2026`, so joining the key path for a title would print raw buckets
back at the reader.

The title uses the FULL chain — `Acme › Bolt · Jan 2026` — because a leaf
label alone stops identifying anything the moment two parents each have a
child by that name, which is the normal case in a pivot. Rows join with
`›` (a path), the column coordinate with `·` (a separate fact). An empty
row path renders "All rows" rather than a blank title.

### The dialog owns its own open state

`DrillDialog` holds the "which cell is open" state itself and is opened
through an imperative handle (`drillRef.current.open(target)`), NOT via a
prop from PivotView.

That is not a style choice. `PivotView` is one ~900-line component with
no internal memo boundary, so a `useState` up there re-rendered the
ENTIRE matrix to display a dialog that changes nothing about the matrix
— measured at ~12% of a full mount to open and ~3% to close, essentially
all of it wasted. A prop would put the state back in the parent and undo
the point.

### `pivotCellRows` must mirror `pivot()` — and no longer sits next to it

`pivot()` filters rows, columns AND values through `disabled`, and the
drill query must filter identically or the dialog hands back rows the
number on screen never counted. The two now live in different files, so
that mirror is held by a TEST (`drill.test.ts`, "ignores a switched-off
dimension") rather than by adjacency. `splitLeafId` deliberately stayed
in `pivot.ts`, beside the code that builds leaf ids — a decoder that
drifts away from its encoder breaks silently.

## Export

The toolbar's **Export to CSV** is mode-aware — one button, because an
export must produce WHAT IS ON SCREEN. In pivot mode it emits the matrix
(`pivotToCsvRows`), not the flat record list, and the scope menu collapses
to a single item: a pivot already summarises every filtered row, so
"current page" would imply a distinction that doesn't exist.

Nested headers flatten to one unambiguous name per column ("North / Q1 /
Sales (sum)") — spreadsheets have no spanning cells, and the agg fn is
part of a measure's identity (two "Rate" columns differing only by
sum/avg must not collide). Numbers export RAW: a CSV is opened to be
computed with, and "$1,234.50" is a string there, not money. An empty
intersection stays EMPTY, never 0 — the same distinction the dash draws
on screen.

## Persistence

One per-table key, `TABLE_PARTS.pivot` (frozen in `registry.test.ts`):
`{ enabled, model } | null`. `enabled` lives INSIDE the object so toggling
pivot off keeps the configuration. The model's arrays mean multi-level
pivoting can arrive **without changing the key's shape**.

A saved tab CARRIES its pivot (`SavedTab.pivot?`), so "Revenue by
customer" can be one tab and the raw list another. Captured on save and
applied on select, following the same rule as the tab's sort: the live
pivot is only RE-captured when you edit the tab you're actually on —
otherwise you'd stamp this tab with another tab's report. A tab saved
before pivot existed carries none, and selecting it leaves the current
report alone rather than silently switching it off.

## Phase 1 boundary

Ships: N row fields (an expand/collapse tree) × N column fields (nested
header levels) × N values, `(n)` counts, grand-total row, matrix CSV,
derived date grains, persistence.

Rows nest as a TREE: every level gets its own aggregate row, so a
collapsed parent still shows a real total rather than a blank. Collapse
state is SESSION state, not a preference — it's a reading position, and
restoring yesterday's half-open tree would surprise more than it helps.
Default is expanded, so data is visible before the chevron is learned.

**Not built** (measured against MUI's pivoting docs — the full list, so
nobody has to rediscover a gap):

| Missing | Note |
|---|---|
| controlled props (`pivotModel`/`pivotActive`/`pivotPanelOpen`) | ours is uncontrolled + persisted; add when a page must own the state |
| sticky column-GROUP labels | we sticky the row-label column instead |

## The panel is structured like MUI's

An **unassigned pool** at the top holds every field that isn't placed,
each with a `+`; below it three **collapsible sections** hold only what
you actually assigned, in nesting order. Each assigned field carries a
drag handle, a checkbox, its aggregation chip (Values) and a **⋮ menu**:
Move up / down / to top / to bottom · send to Rows / Columns / Values
(checked where it currently sits) · Remove.

The first design listed EVERY field inside EVERY section with a
checkbox. Three problems that compound as a grid gains columns:

- the same field appeared three times, once greyed with an "in Rows"
  tag, so one badge-shaped token meant *count* on a header and *nesting
  order* on a row;
- the list was 3n rows long to express n decisions;
- nesting order was **displayed and not editable** — the only way to
  reorder was to untick everything and re-tick in the order you wanted.

Search now filters the **pool only**. Filtering the sections too meant
typing a query could hide fields you had already assigned, leaving the
count badges reading 2/1/1 over three empty lists with no way to remove
anything.

### Panel composition (layout-audit rules, 2026-07-29)

The first `ux-layout-composition-audit` ran here and its fixes are
load-bearing — don't regress them:

- **Zones are boxed, the pool is bare.** Rows/Columns/Values each get
  `rounded-md border bg-muted/30` inside a `p-2 space-y-2` stack, so
  between-zone air exceeds within-zone rhythm and the eye can count
  the regions (Gestalt common region). The pool deliberately has NO
  box — enclosure is what says "take from here, drop into there" —
  but it does have a heading ("Available fields" + count, the same
  words the screen-reader announcements use).
- **An empty zone keeps a drop well**: a dashed, `min-h-9` bordered
  well holding the axis hint — a target must exist and have area
  BEFORE the drag starts; it turns primary when it's the pending
  destination.
- **`required` is a state chip**, warn-toned while the zone is empty,
  quiet-bordered once satisfied — never plain text in the heading's
  own grey.
- **Pool rows reserve the checkbox column** (`w-3.5` spacer) so labels
  sit on one x in every region.
- **Target sizes (S5, verified by circle math — don't eyeball):**
  row micro-controls (grip / + / ⋮) carry invisible `p-1` → 22px hit
  boxes that conform via WCAG 2.5.8's spacing exception, some on
  **1.5–2.5px margins** (zone grip ↔ checkbox) — widening ANY of them
  further breaks a neighbour's exception; recompute before touching.
  The search-clear ✕ is a true 24×24 (`p-1` + 16px icon) because it
  nests inside the input, where no exception is available. The resize
  handle is 8px (`-left-1 w-2`) and honestly BELOW the floor — an edge
  separator has nowhere to grow: reaching further inward puts it on
  top of the pool grips at z-10 (verified regression, reverted).
- **The zone header is a BAND, not the first row** (second audit run,
  after the owner compared against MUI's two-band grammar): `bg-muted/70`
  fill + a `border-b` hairline while open, one treatment on all three
  zones — the control that governs a group sits on a different plane
  than the group's members (S1 region-anatomy check, added to the
  skill from this exact finding).

### Only ROWS are required

A report renders as soon as it has one row dimension. Measures and
column dimensions are REFINEMENTS: switch every measure off and you
still get the groups and their counts (`Disney Studios (20)`), which is
a real answer to "how do these 500 rows split by customer?".

This matters most while you are experimenting. Requiring a measure meant
unticking your last one replaced the whole report with an empty state —
taking the configuration you had just built off screen at exactly the
moment you were toggling something to compare with and without. MUI
behaves the same way, for the same reason.

With no measure there is still ONE leaf column per column bucket, so the
column-group headers keep something to span and the matrix keeps its
shape; every cell is `null`, painted as a dash. With neither columns nor
measures the header collapses to a single empty level so the corner cell
(the row field's name) still has a row to live in.

The guard is on the RESOLVED dimensions, not the raw keys — a saved
model naming a column the grid no longer has would otherwise render a
table with no row identity at all.

### The checkbox switches a field OFF — it does not unassign it

Unticking keeps the field exactly where you put it: same section, same
place in the nesting order, same aggregation for a measure. It just
stops contributing, so "show me this report without Driver" is one
click and one click back.

It used to REMOVE. Two consequences, both reported from the live UI:
unticking a Columns field made it jump back to the pool (losing its
position), and unticking your only measure blanked the whole report to
the "choose a value" empty state — from which the configuration you'd
built was no longer visible.

Stored as `PivotModel.disabled: string[]`, deliberately **additive**: a
model saved before this existed has no such list, which reads correctly
as "nothing is off". Removing is the ⋮ menu or a drag back to the pool.

⚠️ `pivot()` filters rows, columns AND values through `disabled`, and
**`pivotCellRows` must filter identically** — a drill-down that matched
on a switched-off dimension would hand back rows the number on screen
never counted. A test pins that.

⚠️ **`disabled` ⊆ assigned.** `prunePivotModel` sweeps entries for
fields that are no longer on any axis, so the invariant holds however
the model was written. Enforcing it there rather than at each unassign
site is deliberate: switching a field off and then removing it used to
leave a ghost entry, so re-adding it later brought it back already
unticked with nothing on screen explaining why.

### Dragging

Drag runs between EVERY list — pool ↔ Rows ↔ Columns ↔ Values — and
within one, on `@dnd-kit` (the same dependency the grid uses for column
reorder). Dragging a field back to the pool is how you unassign it by
gesture. The ⋮ menu and the pool's `+` do the same jobs by click, since
a drag is a poor fit for keyboard and touch.

Three rules the implementation holds to:

- **Nothing commits until drop.** Hover computes where the field *would*
  land and renders that; the model is untouched. A drag abandoned
  halfway leaves the report exactly as it was — mutating on `dragOver`
  (the common dnd-kit recipe) would write a persisted preference on
  every pointer move.
- **Two cues, because they answer different questions.** The dragged
  field rides the cursor in a `DragOverlay` (without it the row vanishes
  from one list and reappears in another, which reads as a glitch); the
  target list takes a **ring**, and an **insertion rule** marks the
  exact index. The ring alone tells you the section — but order IS
  nesting here, so position needs its own mark.
- **Rows do NOT shuffle.** `useSortable`'s `transform`/`transition` are
  deliberately not applied. The sortable strategy opens a gap computed
  from dnd-kit's own index within ONE list — and because we never mutate
  on hover, a list the field is being dragged INTO doesn't know it is
  coming, so that gap lands in the wrong place (or never opens) while
  the insertion line says something else. Two indicators that disagree
  exactly where it matters most. One indicator, correct everywhere:
  rows hold still, the line moves. MUI's panel behaves the same way.
  Hovering a field over its own slot draws no line at all — that drop
  is a no-op, and a line promising a move that won't happen is worse
  than no line.
- **Collision detection is `pointerWithin`, never rectangle overlap.**
  Rect-based detection lights up whichever zone the dragged item's BOX
  intersects, which is not where the user is aiming — with the cursor
  over the grid, a zone hundreds of pixels away would highlight and take
  the drop. It also means nothing highlights when the cursor is outside
  every zone, which is the honest answer there.
- **The overlay is a compact chip offset down-right of the pointer.** A
  full-width pill sat exactly on the zone heading it was hovering, so
  the one label you need to read was the one thing hidden.
- **Keyboard drag actually works.** dnd-kit puts `aria-describedby` on
  every handle promising space-bar pickup; shipping that without a
  `KeyboardSensor` announces a capability that doesn't exist (WCAG
  2.1.1). Announcements are written in task language too — the defaults
  read out our internal ids ("dropped over droppable area
  rows:company_code"), which describes the data model, not the move.
- **The drop lands where the LINE drew it.** The line means "insert
  before the item at this index of the list as displayed" — and that
  list still contains the field being dragged. Placing it removes it
  first, which shifts every later position up one, so a DOWNWARD move
  within one list must decrement (`insertionIndex`, pure + tested).
  Routing same-list drops through `arrayMove` instead was a real bug:
  its `to` is a FINAL index, not an insert-before one, so a field
  dragged down landed one slot past the line. Every drop now takes ONE
  path, so the promise and the result can't drift apart.
- **A drop target only lights up if it can accept the field.** Legality
  comes from the same `pivotable` / `aggregable` opt-ins as everything
  else, so a customer name can never be dragged into Values. The pool
  always accepts — it means "unassign".

Deliberate DIFFERENCES from MUI (not gaps): `pivotable` is opt-**in**
here (our grids carry 15+ columns, so opt-out would make the picker
useless); the pivot toggle lives in the toolbar rather than inside the
panel (the panel is transient, the toggle shouldn't vanish with it);
there is no `disablePivoting` — a grid simply doesn't pass `pivot`.

⚠️ **Client-complete data only.** Pivot aggregates the rows the grid
holds; on a server-paged grid it would summarise one page and present it
as the whole truth.
