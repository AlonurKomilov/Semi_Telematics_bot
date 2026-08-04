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
  [`components/datagrid/DataGrid.tsx`](src/components/datagrid/DataGrid.tsx).
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
- **Checkbox = membership · Switch = behaviour · pressed button = a
  behaviour in a bar.** A checkbox answers *"is this ITEM in the set?"*
  (which fields, which columns) — it belongs in lists of things, and
  several in a row read as one multi-select. `<Switch>`
  ([`ui/switch.tsx`](src/components/ui/switch.tsx)) answers *"is this
  BEHAVIOUR on?"*. A binary that must live in a toolbar or header
  instead is a **pressed icon-button** (`aria-pressed` + filled
  `variant` when on). Never mix shapes in one vertical run: the pivot
  panel stacked a zone setting directly above its field rows as the
  same checkbox, so five identical boxes in one column meant two
  unrelated things and every label had to be read to tell them apart.
  **A setting that governs a rendered COLUMN belongs on the `⋮` of the
  field(s) that produce it** — the same place a DataGrid column keeps
  Pin and Hide — not inline among the items it governs. When several
  fields jointly produce one column, put the item on each of them
  showing one shared state, and name it after the OUTPUT ("Pin row
  labels"), never after the field you opened ("Pin Company") — the
  latter promises a per-field effect that doesn't exist.
- **Mine-vs-shared = always "My X" / "Shared".** Any feature with an
  account-owned side and a cross-account side (Vendors, Parts, Service
  Tasks, assemblies next) labels the split with those two words —
  page tabs, merge-scope pickers, picker group headings, and the
  Source badge (`Mine` / `Shared`). Do NOT invent a per-feature noun
  for the shared half (Directory, Catalog, Standard, Library): the
  user is answering one binary — *mine or everyone's?* — and a
  different vocabulary per feature is a learning cost with no payoff.
  The industry noun still earns its keep in **explanatory copy**
  ("the same shop as a public directory entry") where it names the
  specific thing rather than the tab.
- **Role words never in shared identifiers or shared copy.** Persona
  words (`fleet`, `safety`, …) are live role identifiers (subdomains,
  shells); role-flavored text ("Fleet Overview") is GENERATED from the
  active view. Shared data/props/types use the domain noun (`vehicles`,
  not `fleet` — e.g. `stats.vehicles ?? stats.fleet`, `VehicleStats`).
  Persona words belong only in per-role artifacts (`FleetHero`,
  `SafetyShell`). Full rule:
  [docs/architecture/PERSONA.md](../../docs/architecture/PERSONA.md)
  §"Naming: role words vs domain nouns".
- **Hover-info & explanations = the tooltip family, never native `title=`.**
  [`components/tooltip/`](src/components/tooltip/) is the SSOT: `<Tip>` for
  hover labels, `<Freshness>` for data-age, `<InfoTip>` for learn-once field
  explanations (a muted ⓘ; also what `PageHeader description` renders as).
  Native `title=` is banned (unthemed, invisible on touch — ESLint warns);
  keep `aria-label` on icon-only controls. Icon semantics: ⓘ = explanation ·
  (?) = how-to · (!) = warnings. Full rules (what stays visible, the
  toggletip-vs-hover split, provider):
  [components/tooltip/CLAUDE.md](src/components/tooltip/CLAUDE.md).
- **Time windows = `DateRangePresets`, always.** Never hand-roll a days
  dropdown / chip row / numeric days input — use
  [`components/shell/DateRangePresets.tsx`](src/components/shell/DateRangePresets.tsx)
  (`variant="dropdown"` for toolbars/forms, `variant="segments"` for
  chart/section headers; one `days` contract). NOT for config values that
  merely happen to be day counts (link expiry, rule periods). Variants,
  the `end=`/fail-closed rule, and props:
  [components/shell/CLAUDE.md](src/components/shell/CLAUDE.md).
- **Scrolling panes = `components/scrolling`.** A `overflow-y-auto` div
  is not a scroll region; it is a box that clips. `<ScrollRegion>` (or
  `useScrollRegion()` when you own the div) adds the four things missing
  by default: `tabIndex` — a plain overflow div is NOT focusable, so a
  keyboard user cannot scroll it at all (**WCAG 2.1.1**); a named
  `role="region"` when you pass a label; `overscroll-contain`; and
  `scroll-padding` so the browser's scroll-into-view stops short of a
  sticky header instead of parking focus behind it (**WCAG 2.4.11**).
  The app had 57 scrolling surfaces and 2 that got this right.
  ⚠️ **Not everything that scrolls is a region** — a short menu,
  dropdown or picker list is correctly a plain overflow div, and
  wrapping one in a landmark makes the page noisier, not clearer.
  **Modals are never hand-rolled**: `<Sheet>` for a side drawer,
  `<Dialog>` for a centred one — a bare `fixed inset-0 bg-black/…`
  backdrop has no focus trap, no Escape, no `aria-modal` and no
  background scroll lock. ESLint flags it — as a **warning**, not an
  error, because 14 hand-rolled backdrops still exist and erroring would
  break the build before they're converted; it is one line in a
  200-warning pile, so treat it as a to-do list rather than a guard.
  Full rules, the
  refusal list, and what the module deliberately does NOT absorb:
  [components/scrolling/CLAUDE.md](src/components/scrolling/CLAUDE.md).
- **Who-did-what = `components/activity-trail`, never "history".** The
  word *history* is already taken by a different concept —
  `features/maintenance/ServiceHistoryModal` shows a vehicle's past
  SERVICES. The audit trail (who changed what, field-level old→new)
  takes its name from the capability that owns it
  (`capabilities/activity_trail`, table `activity_events`, `/activity`
  endpoints), so one search for *activity* finds the whole stack.
  Render every trail surface through `ActivityTrailList` /
  `ActivityTrailDialog` — never a second renderer. Product wording may
  still say "activity history" to users; two CODE names for one concept
  is what this rule forbids.
- **Tables = DataGrid, always.** Any tabular list of records — even a
  5-row read-only summary — uses
  [`components/datagrid`](src/components/datagrid/DataGrid.tsx); **never** a
  raw `<table>` for a data list (narrow exceptions: permission **matrices**,
  **form-embedded** line-item editors, **headerless layout** scaffolding).
  Minimal display: `enableToolbar={false} enablePagination={false}`.
  DataGrid is the SSOT for card / zebra / sort / pin / filter / pagination /
  theme AND for these opt-in capabilities — pass the prop, never hand-roll:
  **bulk select + action bar** (`bulkSelection` + `bulkActions`), **footer
  aggregation** (`aggregable` per column), **column filters** (`filterable`
  + `filterMode` select/range/date-range, chosen by cardinality),
  **lifecycle tabs** (`segments`; live counts → feature hero), **personal
  saved tabs** (`savedTabs`, managed by right-click), **right-click row
  menus** (`rowActions`). A grid also self-enforces a floor (can't hide to
  zero columns) and auto-renders active filter/sort/search/grouping as
  removable chips. **Never hand-roll a tab row, a filter chip strip, or a
  totals row beside a grid** — `segments` is the SSOT for slicing one
  grid (`FilterChips` is for surfaces with NO grid). The governing rule
  is *declare, don't implement*: a feature hands DataGrid column /
  segment / action DATA and never the mechanism — if a page holds
  `useState` for a filter, a `forEach` counting rows for a badge, or JSX
  for a control the grid already has, it is in the wrong place. Full
  rules, both segment shapes (local `match` vs server-controlled), prop
  contracts, gotchas, and worked examples:
  [components/datagrid/CLAUDE.md](src/components/datagrid/CLAUDE.md).
- **Right-click / action menus = the shared `context-menu` primitive.**
  Never hand-roll a menu. Declare actions as `MenuAction[]` data and open
  via `<ContextMenu>` (right-click), `<ActionMenu>` (click ⋮/button), or
  DataGrid's `rowActions` prop; a feature's action list lives in
  `features/<x>/contextMenu.tsx`. Full rule + contracts:
  [components/ui/CLAUDE.md](src/components/ui/CLAUDE.md).
- **Per-user UI state = the preferences service, never raw `localStorage`.**
  `usePreference('notif.position')` (or `preferences.get(…)` outside React);
  add ONE entry to [`src/preferences/registry.ts`](src/preferences/registry.ts)
  with its type, default, `scope` (`device` vs `synced`) and legacy key.
  Keys are FROZEN — renaming one silently orphans that user's data. If the
  BACKEND reads the value to act on it (DND, timezone, language) or it
  affects anyone else, it's a typed column / feature table instead. Full
  rule + how to add one:
  [src/preferences/CLAUDE.md](src/preferences/CLAUDE.md).
- **Charts/maps** can't use classes: charts → `chartColor(n)` (the
  `--chart-1..5` tokens); map hex → a shared config constant, never inline.

This file governs `interfaces/dashboard` only. The `system_dashboard`
(operator console) and `miniapp` (Telegram) are separate design languages
by intent — see design.md §9.
