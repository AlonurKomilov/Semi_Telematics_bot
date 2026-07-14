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
- **Compose primitives.** Build from [`src/components/ui/`](src/components/ui/)
  and [`src/components/shell/`](src/components/shell/) — don't re-implement
  buttons, badges, dialogs, empty/error/loading states.
- **Hover-info = the tooltip family, never native `title=`.**
  [`components/tooltip/`](src/components/tooltip/) is the SSOT (same idea as
  DataGrid for tables): `<Tip label="…">` for plain hover labels,
  `<Freshness ts={…}>` for data-age indicators. Native `title=` renders the
  browser's unthemed, delayed tooltip and is invisible on touch — an ESLint
  warn flags it on DOM elements; migrate legacy usages as you touch files.
  Keep `aria-label` on icon-only controls (the tooltip is not the
  accessible name). One `TooltipProvider` is mounted in main.tsx — never
  add per-instance providers.
- **Tables = DataGrid, always.** Any tabular list of rows — even a
  5-row read-only summary — uses
  [`components/DataGrid`](src/components/DataGrid.tsx). It's the
  single source of truth for card / header tint / zebra rows / column
  sort / pin / filter / pagination / theme awareness. **Never** roll a
  raw `<table>` for a data list. For minimal display tables (no
  chrome needed) pass `enableToolbar={false} enablePagination={false}`
  and DataGrid renders just the header + rows inside a bordered
  card. `firstColumnLeading={{ header, cell }}` attaches a bulk-select
  checkbox (or expand toggle, row-number, …) that follows whichever
  column is currently leftmost. The narrow exceptions where raw
  `<table>` is still correct: (a) permission / config **matrices**
  (form UI, not a list), (b) **form-embedded** line-item editors
  (Work-order parts), (c) **headerless layout tables** used as form
  scaffolding (Forum routing rows). If it's a list of records the
  operator would want to sort or filter, it's DataGrid.
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
- **Charts/maps** can't use classes: charts → `chartColor(n)` (the
  `--chart-1..5` tokens); map hex → a shared config constant, never inline.

This file governs `interfaces/dashboard` only. The `system_dashboard`
(operator console) and `miniapp` (Telegram) are separate design languages
by intent — see design.md §9.
