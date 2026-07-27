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

## Opting a column in

- **Dimensions** (Rows / Columns pickers): `pivotable: true`
- **Measures** (Values picker): the existing `aggregable: true`
- A dimension can bucket: `pivotValue: (row) => string`. That's how a
  timestamp becomes a MONTH — and it **must** use the account timezone
  (`monthInTimeZone` in utils/datetime.ts). A load delivered 23:00 on the
  31st in Denver is the 1st in UTC, so a UTC bucket files its revenue in
  the wrong month.
- `pivotLabel: (bucket) => string` pretty-prints it (`2026-01` → `Jan 2026`).
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

## Persistence

One per-table key, `TABLE_PARTS.pivot` (frozen in `registry.test.ts`):
`{ enabled, model } | null`. `enabled` lives INSIDE the object so toggling
pivot off keeps the configuration. The model's arrays mean multi-level
pivoting can arrive **without changing the key's shape**.

Saved tabs stay orthogonal for now — `SavedTab` is a stored object, so an
optional `pivot?` field can be added backward-compatibly later. The name
is reserved; don't build it until asked.

## Phase 1 boundary

Ships: 1 row field × 1 column field × N values, `(n)` counts, grand-total
row, persistence.

**Deliberately deferred** — don't add without a reason: drag-and-drop
(nothing to reorder at 1×1), multi-level rows/columns + expand/collapse,
pivot-side sorting, cell drill-down, saved-tab capture, matrix CSV export.

⚠️ **Client-complete data only.** Pivot aggregates the rows the grid
holds; on a server-paged grid it would summarise one page and present it
as the whole truth.
