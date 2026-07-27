# components/datagrid/pivot — cross-tab reporting

Opt a grid in with the **`pivot`** prop (needs `tableId`). A toolbar
**Pivot** toggle swaps the record list for a matrix: rows × columns ×
aggregated values, with a **Fields** panel to configure it.

```
pivot.ts        the pure transform (+ pivot.test.ts) — no React
PivotView.tsx   the read-only matrix renderer
PivotPanel.tsx  Rows / Columns / Values pickers
```

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

Two rules the tests pin: sorting happens WITHIN each parent, never
across the flat list (that would tear children out of their group); and
a row with NO value in the sorted column sinks to the bottom in BOTH
directions — absent is not "smaller than every number". A sort naming a
leaf that no longer exists falls back to label order rather than
discarding the report.

## Drill-down

Every non-empty cell is a button: clicking it opens the SOURCE ROWS
behind that number. Rows are recomputed on demand (`pivotCellRows`) —
caching them per cell would hold an array for every cell in the matrix to
answer a question asked about one.

Clicking a COLLAPSED PARENT drills its whole subtree, because the
parent's number IS the sum of its descendants — a drill-down that showed
fewer rows than the number accounts for would be lying. A test asserts
the drilled rows re-aggregate to the figure on screen.

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
| drag-and-drop reorder | checkbox pickers cover 1–2 levels |
| unassigned-field list with `+` | we list every field in each section instead |
| collapsible panel sections, per-field ⋮ | no per-field actions yet to hold |
| controlled props (`pivotModel`/`pivotActive`/`pivotPanelOpen`) | ours is uncontrolled + persisted; add when a page must own the state |
| sticky column-GROUP labels | we sticky the row-label column instead |

Deliberate DIFFERENCES from MUI (not gaps): `pivotable` is opt-**in**
here (our grids carry 15+ columns, so opt-out would make the picker
useless); the pivot toggle lives in the toolbar rather than inside the
panel (the panel is transient, the toggle shouldn't vanish with it);
there is no `disablePivoting` — a grid simply doesn't pass `pivot`.

⚠️ **Client-complete data only.** Pivot aggregates the rows the grid
holds; on a server-paged grid it would summarise one page and present it
as the whole truth.
