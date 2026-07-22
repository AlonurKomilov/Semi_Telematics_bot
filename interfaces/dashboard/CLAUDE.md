# Dashboard UI rules

**Before writing or changing any UI in this app, follow the design system
in [design.md](design.md).** It is the single source of truth. Key rules:

- **Colour = token, never literal.** No `#hex` in components. No raw
  Tailwind palette (`text-green-500`, `bg-amber-100`). Use the semantic
  tokens (`bg-card`, `text-muted-foreground`, `bg-primary`, …). This
  includes `index.html` — its only allowed literal is the body's
  always-dark splash `bg`; the body text is `text-foreground`.
- **Declare text colour — never inherit it.** Any text-on-surface element
  (inputs, buttons, chips) sets `text-foreground` / the matching
  `*-foreground`; `bg-<x>` and `text-<x>-foreground` travel together. A
  control with a `bg`/`placeholder` but no value colour inherits the body
  default and goes invisible on the light theme — verify **both** themes.
- **Status/severity = a tone, via the helper.** Import from
  [`src/lib/status.ts`](src/lib/status.ts): `statusClasses(status)`,
  `toneClasses('danger')`, `toneText('warn')`. Add new statuses to the
  `statusTone` map there — never pick a colour at the call-site. The four
  tones are `ok / warn / danger / info` (+ `neutral`).
- **Spacing = 4px scale.** Use `gap-2`, `px-3`, `py-1.5`, etc. No
  arbitrary values (`p-[13px]`, `h-[42px]`) for layout.
- **Radius = `--radius`.** Use `rounded`, `rounded-md`, `rounded-lg`.
  Never `rounded-[10px]` or `rounded-4xl` (ignores the theme picker).
  This includes **JS-drawn geometry** (SVG paths, canvas arcs): a
  corner radius baked into a path number is invisible to the Corners
  picker. Read the live token instead —
  `getComputedStyle(document.documentElement).getPropertyValue('--radius')`
  — and recompute when `<html>`'s theme attributes change
  (MutationObserver), since a CSS-var change alone never re-renders
  React. Reference implementation: `SegmentTab` in
  [`components/DataGrid.tsx`](src/components/DataGrid.tsx).
- **Type = Geist scale, by ROLE.** `text-xs`/`text-sm` body, sub-12px via
  `text-2xs`/`text-3xs` (never `text-[10px]`). Headings use the fixed §4
  role combos so they're identical on every page: **page title** `text-2xl
  font-bold` (use `PageHeader`), **section title** `text-lg font-semibold`,
  **card title** `text-base font-semibold`, **caps label** `text-xs
  font-medium uppercase tracking-wide text-muted-foreground`. Don't
  improvise heading sizes/weights.
- **`font-mono` = machine identifiers only** (IDs, IPs, hashes, tokens,
  code). Never on human-readable data (company codes, names, statuses) — and
  style the same column (Company, status) identically across pages.
- **Icons = lucide-react at a standard size.** `12 · 14 · 16 · 18 · 20 · 24`
  (via `size={16}` or `size-4`), coloured by a token. No off-step sizes
  (`size={11}`/`{13}`/`{22}`), no second icon set, no emoji as UI icons.
- **Sizes & layers = scales too (design.md §7).** Controls on
  `h-7 · h-8 · h-9` (`size-7/8/9` icon-buttons); menus `w-44/56/64`, list
  panels `w-80`; new dialogs `max-w-lg/xl/2xl`. Z-index ladder: `0–20`
  content · `30` sticky · `40` panels · `50` menus/dialogs/toasts ·
  `z-[60]` above-dialog (palette/lightbox) · `z-[100]` maintenance blocker
  only. Arbitrary `z-[N]` otherwise banned — ESLint warns; map components
  matching Leaflet pane values are the commented exception.
- **Compose primitives.** Build from [`src/components/ui/`](src/components/ui/)
  and [`src/components/shell/`](src/components/shell/) — don't re-implement
  buttons, badges, dialogs, empty/error/loading states.
- **Role words never in shared identifiers or shared copy.** Persona
  words (`fleet`, `safety`, …) are live role identifiers (subdomains,
  shells); role-flavored text ("Fleet Overview") is GENERATED from the
  active view. Shared data/props/types use the domain noun (`vehicles`,
  not `fleet` — e.g. `stats.vehicles ?? stats.fleet`, `VehicleStats`).
  Persona words belong only in per-role artifacts (`FleetHero`,
  `SafetyShell`). Full rule:
  [docs/architecture/PERSONA.md](../../docs/architecture/PERSONA.md)
  §"Naming: role words vs domain nouns".
- **Learn-once field explanations = `<InfoTip>`, not always-visible text.**
  Helper text a user reads twice and then scrolls past ("saved to the
  history with your next action") collapses behind the muted ⓘ from
  [`components/tooltip/InfoTip`](src/components/tooltip/InfoTip.tsx) next
  to the field label. Page descriptions collapse the same way — the
  `PageHeader description` prop renders as ⓘ beside the title (one
  change in the shell converts every page; keep writing the prop).
  Keep VISIBLE: dynamic/state feedback ("No odometer telemetry…",
  "Saving…") and one-time-user forms (the public apply form — its users
  never get a second visit, so visible help is correct there). Icon
  semantics: ⓘ = explanation · (?) = how-to · (!) = warnings ONLY.
  Behavior split: InfoTip is a TOGGLETIP — opens on CLICK, anchored to
  the ⓘ itself, closes on outside-click/Esc (works on touch; sentences
  survive mouse drift). Tip/Freshness stay hover + cursor-anchored.
  Migrate legacy helper texts as you touch files.
- **Hover-info = the tooltip family, never native `title=`.**
  [`components/tooltip/`](src/components/tooltip/) is the SSOT (same idea as
  DataGrid for tables): `<Tip label="…">` for plain hover labels,
  `<Freshness ts={…}>` for data-age indicators. Native `title=` renders the
  browser's unthemed, delayed tooltip and is invisible on touch — an ESLint
  warn flags it on DOM elements; migrate legacy usages as you touch files.
  Keep `aria-label` on icon-only controls (the tooltip is not the
  accessible name). One `TooltipProvider` is mounted in main.tsx — never
  add per-instance providers.
- **Time windows = `DateRangePresets`, always.**
  [`components/shell/DateRangePresets.tsx`](src/components/shell/DateRangePresets.tsx)
  is the SSOT for "pick a time window" (same idea as DataGrid for
  tables): never hand-roll a days dropdown, a chip row, or a numeric
  days input. Two forms, one `days` contract:
  `variant="dropdown"` (default — toolbars/forms, presets + custom
  calendar) and `variant="segments"` (inline chip row for chart/section
  headers). Pass `options` for page-specific windows, `maxDays` when
  the backend accepts more than 90, `disabled` while generating,
  `isFetching` for the spinner. END dates are per-page and FAIL-CLOSED:
  pass `onApplyRange` + `end` ONLY when the page's backend honors an
  explicit `end=` param (worked example: Safety Events) — otherwise the
  calendar stays start-only and the range honestly ends today.
  NOT this component: config values that
  merely happen to be day counts (link expiry, rule periods, service
  intervals, backfill action menus) — those stay form inputs.
- **Tables = DataGrid, always.** Any tabular list of rows — even a
  5-row read-only summary — uses
  [`components/DataGrid`](src/components/DataGrid.tsx). It's the
  single source of truth for card / header tint / zebra rows / column
  sort / pin / filter / pagination / theme awareness. **Never** roll a
  raw `<table>` for a data list. For minimal display tables (no
  chrome needed) pass `enableToolbar={false} enablePagination={false}`
  and DataGrid renders just the header + rows inside a bordered
  card. For bulk-select checkboxes use the `bulkSelection` prop (see
  the bulk rule below); `firstColumnLeading={{ header, cell }}` is for
  OTHER leading content (expand toggle, row-number, …) that follows
  whichever column is currently leftmost. The narrow exceptions where raw
  `<table>` is still correct: (a) permission / config **matrices**
  (form UI, not a list), (b) **form-embedded** line-item editors
  (Work-order parts), (c) **headerless layout tables** used as form
  scaffolding (Forum routing rows). If it's a list of records the
  operator would want to sort or filter, it's DataGrid.
- **Bulk selection + actions = DataGrid props, never hand-rolled.**
  DataGrid owns row selection (the checkbox column — header select-all
  with indeterminate, per-row, group select-all) and the **bulk-action
  bar** — a TOP strip between the toolbar and the table (icon-only
  buttons with `<Tip>` tooltips, `tone: 'danger'` paints the icon red),
  shown when 1+ rows are selected. Turn it on with `bulkSelection` and
  pass `bulkActions={[{ label, icon?, tone?, confirm?, options?,
  onRun(selectedRows, value?) }]}` — each `onRun` receives the selected
  ORIGINAL rows (never tanstack ids) and DataGrid clears the selection
  when it resolves. `options` makes the button a dropdown (e.g. "Change
  status ▾"), passing the chosen value to `onRun`. `bulkRowLabel` gives
  per-row a11y; `isRowSelectable(row)` gates which rows get a checkbox
  (e.g. only ackable alerts); `onBulkSelectionChange` mirrors the set
  out (e.g. AI page-context). The checkbox lives in its OWN dedicated
  column (a synthetic locked, force-pinned-left, 44px column — id
  `__select__`), NOT riding inside the first data cell — so the select
  box never crowds the Vehicle/Name value. Do NOT re-implement a
  `selectedIds` Set, checkbox `<input>`s via `firstColumnLeading`, or a
  `fixed bottom-4` bar on a page — that's the old copy-pasted pattern
  this replaced. `firstColumnLeading` remains ONLY for genuinely
  non-selection leading content (expand toggle, row-number); when
  `bulkSelection` is also on it attaches to the first DATA column, one
  slot right of the select column. Checkbox selection and Ctrl/Cmd-click
  Copy share one set + one bar. Controlled selection: pass `selectedIds` + `onSelectedIdsChange`
  when a page must OWN the set (e.g. Alerts' shared context); omit both
  for the default DataGrid-owned selection (Alerts Results is the
  controlled example — its selection lives in a shared
  `AlertsSelectionContext`). Active view state auto-renders as removable
  chips inline on the toolbar line, after the bulk bar / headerToolbar
  (nothing to wire per page — driven by the grid's own state): filter ·
  sort · search · row-grouping ("Grouped by X"). Deliberately NO chip
  for: hidden columns (hiding a column is a deliberate layout act, not an
  active view constraint — surfacing it in either a chip OR a count badge
  reads as an unresolved "notification" to clear), pin (visually
  self-evident), and the column-bracket group (column-config, not a
  per-session toggle). There are NO toolbar Filter/Sort buttons — they
  were pure redundancy (their popovers only viewed/cleared active state,
  which the chips now do, and never ADDED a filter). Adding a filter/sort
  is the column ⋮ 3-dot menu; the chips are the active-state display; the
  Manage-columns button is a plain icon (no badge).
- **A grid can never be hidden down to zero columns.** The last visible
  hideable column locks: its "Hide column" item (per-column 3-dot menu)
  disables with a "last column" hint, and its checkbox in the
  Manage-columns popover disables like a required column. An empty grid
  paints blank AND takes its own 3-dot menu with it, so the operator
  would have no way back — the floor is enforced on BOTH hide paths, not
  just the one you're touching.
- **Aggregation = footer totals, opt-in per column.** A column earns a
  footer total (sum / avg / min / max / count, picked by the operator
  from the ⋮ menu → **Aggregate**) by declaring `aggregable: true` on
  its Column config — explicit by design, because the grid has no column
  type system to infer "this is a number" from. Pair it with
  `aggValue: (row) => number` when the cell renders something formatted
  (`"$2,847"`) but the true value lives elsewhere on the row, and
  `aggFormat: (value, fn) => node` to format the total (currency, units)
  — **switch on `fn`** so `count` doesn't render as `$`. For a
  date/timestamp column set `aggType: 'date'` — the menu then offers only
  Min (earliest) / Max (latest), `aggValue` may return a
  Date/ISO-string/ms-number, and the result formats as a day (a bare
  `YYYY-MM-DD` is treated as a tz-neutral calendar day; a full timestamp's
  day is shown in the account tz). Date columns deliberately do NOT offer
  Count — it's the whole-view row count, not "how many rows have a date",
  so on a nullable column (`reviewed_at`) it misleads; put Count on a
  count column or read the pagination total. Narrow further with `aggFns`
  (default: number → all five, date → min/max). A missing numeric
  (null/undefined/'') is EXCLUDED from sum/avg/min/max, never folded in as
  0. The
  chosen model
  persists per-user (`table.<id>.aggregation`, so it needs `tableId`);
  `defaultAggregation={{ key: fn }}` starts it on. The total reduces over
  the **filtered** set (all pages), and the function name shows as a
  muted micro-label under the header (MUI's "Gross / sum"). Do NOT
  hand-roll a totals row under a grid — this is the SSOT. When row
  grouping is ALSO active, each group row shows that group's totals
  aligned under their columns (the group identity stays pinned at the
  left edge during horizontal scroll), while the footer keeps the grand
  total — matching MUI. This per-group render is skipped when the page
  supplies a custom `rowGroupHeader` or `firstColumnLeading.groupHeader`
  (those own the group row) and keeps the classic full-width label.
  (Phases 1+2 shipped: footer + per-group. Custom functions = Phase 3.)
- **Column `filterable` = by cardinality, not by reflex.** Set
  `filterable: true` on a column when the dropdown will actually help
  an operator narrow the list. Two supported filter modes on the
  Column config:
  - `filterMode: 'select'` (default) — the multi-select checkbox
    popover. Right when there are **≤ ~30 distinct values** that fit
    an enum (Type / Company / Status / Priority / Role). Provide
    `filterValue: (row) => code` + `filterLabel: (row) => nice`
    when the raw match-value differs from the display (e.g. `"oil"` →
    `"Oil Change"`).
  - `filterMode: 'range'` — Min / Max number-input pair. Right for
    **continuous numeric** columns (percentages, mileage, hours,
    counts). Configure `filterRange: { min?, max?, step?, unit? }` —
    omit `min`/`max` to auto-compute from live data. Provide `step` to
    match display precision (`1` for percentages, `1000` for miles).
  - `filterMode: 'date-range'` — From / To native `<input type="date">`
    pair. Right for **date / timestamp** columns (Submitted / Due date /
    Updated / Created). Filter value shape `[isoFrom|null, isoTo|null]`
    (YYYY-MM-DD); the "To" bound is inclusive-to-end-of-day so a
    single-day filter keeps the whole day. Bounds auto-compute from
    live data (earliest / latest).
  - **Skip filterable entirely** for **free-text uniques**
    (Vehicle name, description) — the dropdown becomes a scroll-forever
    list of every value, slower than typing into the global search box.
    Extend `searchKey={[…]}` instead so the search field matches those
    columns. For **address**-style columns where the raw value is
    unique but the city / state groups many rows, opt into
    `filterMode: 'select'` with a `filterValue: (row) => extractCityState(row.address)`
    accessor — the same pattern that turns Vehicle addresses into a
    tractable "Battle Creek, MI (3)" list.
- **Lifecycle tabs = DataGrid `segments`; live counts = feature hero.**
  When a dataset has ONE dominant lifecycle dimension (Active/Archive,
  pipeline stages), pass `segments` to DataGrid — folder-style tabs
  above the toolbar with live counts; every page load starts on the
  FIRST tab (the working set — selection is session-only by design,
  never persisted) — instead of hand-rolling a chip row on the page. Fine-grained stage slicing
  stays in column filters (e.g. a derived-status `filterValue`), and
  feature-level live counts go in the TOPBAR hero: export a
  `<Feature>Hero` from the feature folder and register it in
  [`shells/heroes/featureHeroes.tsx`](src/shells/heroes/featureHeroes.tsx).
  A hero MUST read the same react-query hook + classifier module the
  page uses (shared cache — never a second fetch or a re-implemented
  computation), so its numbers can't drift from the page's. Worked
  examples: `features/maintenance` (useMaintenanceTasks + MaintenanceHero)
  and `features/applications` (useApplications + ApplicationsHero).
- **User-managed tabs = the `savedViews` prop (personal scope tabs).**
  Opt a `tableId` grid in with `savedViews`, and a "+ New view" affordance
  lets an operator save the CURRENT filters + search as a named tab. Key
  design: a view applies as an ISOLATED SCOPE, not a removable filter — it
  becomes a `DataGridSegment` whose `match` is the captured filters, so it
  flows through the exact `sourceData.filter(match)` scoping as Active/
  Archive (no cross-tab leak; sort/export/select-all stay inside). The
  matching reuses `rowPassesColFilter` (in
  [`datagrid/savedViews.ts`](src/components/datagrid/savedViews.ts), pure +
  tested) so a view scopes identically to its live filters. Views persist
  per-user (`table.<id>.views`), sit after built-in `segments` (an implicit
  "All" leads when there are none), and a view saved on a built-in segment
  COMPOSES with it. Don't hand-roll saved-filter tabs on a page — this is
  the SSOT. (Phase 1: filter+search views. Sort/group/column capture,
  reorder, and account-shared views are later phases.)
- **Charts/maps** can't use classes: charts → `chartColor(n)` (the
  `--chart-1..5` tokens); map hex → a shared config constant, never inline.

This file governs `interfaces/dashboard` only. The `system_dashboard`
(operator console) and `miniapp` (Telegram) are separate design languages
by intent — see design.md §9.
