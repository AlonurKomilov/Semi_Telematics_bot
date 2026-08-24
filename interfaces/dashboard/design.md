# 4truck Dashboard — Design System

> **Single source of truth for the customer dashboard's look & feel.**
> Read this before building or changing any UI. The goal is *consistency
> over cleverness*: this is a fleet-operations tool, not a marketing page.
> When a rule here conflicts with a "nicer-looking" one-off, the rule wins.

This document describes what already exists in the code (it is not
aspirational) plus the one layer that was missing — semantic status
tokens. Values live in [`src/index.css`](src/index.css) and
[`tailwind.config.js`](tailwind.config.js); this is the human-readable
contract over them.

Scope: `interfaces/dashboard` (customer app, `dash.4truck.us`). The
operator console (`system_dashboard`) and the Telegram `miniapp` are
**separate design languages by intent** — see [§9](#9-the-other-two-apps).

---

## 1. Principles

1. **Token, never literal.** No `#hex` in components, no raw Tailwind
   palette (`text-green-500`) for meaning. Reach for a token or a helper.
2. **Compose, don't reinvent.** A new screen is existing primitives
   (`ui/*`) and sections arranged — not new buttons/cards from scratch.
3. **Theme-driven.** Colour, radius, and every LENGTH flow from CSS
   variables the user can re-skin from the theme picker. Hardcoding any
   of them breaks that switch silently — see §5.1 for how size works.
4. **Dense and quiet.** `text-xs`/`text-sm` are the body sizes here;
   colour is reserved for *signal* (status), not decoration.

---

## 2. Colour tokens

All colours are CSS variables (oklch) that flip between light `:root`
and `.dark`. Use them through Tailwind classes — `bg-card`,
`text-muted-foreground`, `border-border`, etc. **Never** write the raw
oklch or a hex equivalent.

### Surfaces (elevation ladder)
| Token | Use |
|---|---|
| `background` | The canvas behind everything |
| `sidebar` | Persistent chrome (nav + topbar) — a distinct cooler plane |
| `card` | Standalone content cards |
| `popover` | Menus, dropdowns, dialogs (sits above cards) |
| `muted` | Subtle fills — progress tracks, empty cells, hover |

### Text
| Token | Use |
|---|---|
| `foreground` | Primary text |
| `muted-foreground` | Secondary / labels / metadata |
| `*-foreground` | On-colour text (e.g. `primary-foreground` on `bg-primary`) |

### Declare colour — never inherit it ⭐

**Every element that renders text on its own surface must set an explicit
foreground token.** Don't rely on the inherited `color` — inheritance pulls
whatever some ancestor last set, and that ancestor may not flip with the
theme.

Why this matters (a real bug this rule exists to prevent): `<body>` used to
carry an always-dark splash background, so its text default was historically
a raw near-white. Form controls (`Input`, `Textarea`) declared a `bg` and a
`placeholder:` colour but **no value colour** — so the typed text inherited
near-white. On the **dark** theme (dark field) it was visible; on the
**light** theme (white field) it was white-on-white — invisible. The field
looked empty even though text was there.

A second bug from the same family, and why the token pairs matter: the
`destructive` colour was registered in `tailwind.config.js` **without** a
`foreground` key, so `text-destructive-foreground` — used on five delete /
disconnect buttons — matched no rule and the label fell back to inherited
`--foreground`. That made the label colour an accident of the theme, and
both themes failed AA on it (4.15:1 on light, 3.03:1 on dark). Note the fix
is asymmetric on purpose: light's `--destructive` is dark enough to carry a
near-white label (4.56:1), dark's is light enough to need a near-black one
(6.26:1). **Measure before changing either.**

### The pre-paint theme stamp ⭐

`index.html` opens with an inline `<script id="theme-boot">`. It reads the
stored theme and stamps `<html>` — the `dark` class plus `data-theme` /
`data-radius` and the `--size-*` multipliers — **before the first paint**.
Everything else
about theming depends on it, so it is worth knowing why it exists and what it
may not do.

**Why it can't be React's job.** `ThemeProvider` applies the theme from an
effect, which runs *after* the first paint, and the module bundle only
executes once it has been fetched and parsed. Whatever paints before that is
governed by the document alone. That gap used to be papered over with
`bg-gray-950` on `<body>` — an always-dark splash. It spared dark-theme users
a white flash and handed light-theme users something worse: nothing ever
removed the class, so the dark canvas stayed under the app permanently and
showed through **every surface that isn't full-viewport** — the boot spinners
in `App.tsx`, the maintenance overlay. (The public-apply branch escaped it
only because `main.tsx` resets `body.className` for its own mount.) With the
stamp in place `<body>` needs no literal at all: `body { background-color:
var(--background) }` resolves correctly in both themes, and the stylesheet is
a render-blocking `<link>`, so it is parsed before anything paints.

**Four rules the script must keep** — `src/test/themeBoot.test.ts` runs the
real script out of `index.html` against the real `applyTheme` and enforces
all four:

1. **Read-only.** `preferences/local.ts` is the single writer of that key and
   owns the legacy copy-forward; a second writer breaks that contract.
2. **Skipped on the apply host** — same predicate as `main.tsx`. That surface
   owns its own theme via `applyPublicFormTheme`.
3. **Hand-written, never build-generated.** Tailwind's content scanner reads
   this file for class names; generated markup is invisible to it.
4. **In step with `applyTheme` and the registry defaults.** The script runs
   before any module exists, so it re-states the enums and defaults as
   literals — a duplication that is only safe because the test compares the
   two on every valid input, on garbage, on nothing, and on the legacy key.

**Verify against a build, never the dev server.** `npm run dev` injects CSS
via JS and so does not reproduce the pre-JS window at all. Use
`npm run build && npm run preview`, with storage both seeded and cleared.

Rules that fall out of this:
- A form control / button / chip / any text-on-surface element **declares
  `text-foreground`** (or the matching `*-foreground`), never inherits.
- A `bg-<surface>` and its `text-<surface>-foreground` travel **together**
  — `bg-popover` ⇒ `text-popover-foreground`, `bg-primary` ⇒
  `text-primary-foreground`. Setting one without the other is the smell.
- **Check both themes.** A colour can be fine in one and invisible (or
  low-contrast) in the other. "Works in dark" is *not* "works" — eyeball
  light **and** dark before calling a surface done.

### Identity & interaction
| Token | Use |
|---|---|
| `primary` | Primary actions; brand accent (blue in dark, near-black in light) |
| `secondary` | Secondary buttons / quiet fills |
| `accent` | Hover/active background for interactive rows |
| `border` / `input` | Hairlines and field outlines |
| `ring` | Focus glow (primary @ 50% alpha) |
| `destructive` | Destructive **actions** (delete buttons). Not for status — use `danger`. |

The `brand.50…900` scale also exists for the rare spot needing a literal
brand-blue ramp (e.g. a gradient). Prefer `primary` for anything themed.

---

## 3. Status tokens — the meaning layer ⭐ (the new piece)

Four semantic hues answer "what does this state mean". They are the
**only** correct way to colour a status, severity, or health signal.

| Tone | Token | Means | Example states |
|---|---|---|---|
| 🟢 ok | `--ok` | good / running / done | moving, active, completed, paid, healthy |
| 🟡 warn | `--warn` | attention / pending | idle, pending, high-priority, degraded |
| 🔴 danger | `--danger` | bad / urgent | stopped, overdue, critical, failed |
| 🔵 info | `--info` | in-progress / neutral-active | in_progress, scheduled, medium |
| ⚪ neutral | `muted` + `border` | no signal | off, cancelled, draft, unknown |

These flip light↔dark automatically (dark green text on white →
light green text on black) — one class, both modes.

### The soft-pill recipe
Every badge/chip uses one treatment: a **15% tinted fill + solid text +
30% border**, in one hue. Each tone exposes three tokens for this:
`text-<tone>` (solid), `bg-<tone>-bg` (fill), `border-<tone>-bd` (border).

**Geometry is part of the recipe too** — the reference is
[`StatusBadge`](src/components/StatusBadge.tsx): **`rounded-md · px-2
py-0.5 · text-xs font-medium`**. One corner shape everywhere; a status
pill is never `rounded-full` on one page and square-ish on another.
(Dense table cells may drop to `text-2xs`, keeping the rest.)  Close ✕
icons follow the same two-step idea: `12` inside chips/tags, `16` in
modal/panel headers.

> The `-bg`/`-bd` tokens pre-bake the alpha (via `color-mix` in
> [index.css](src/index.css)) so the recipe is one short class set.
> `bg-ok/15` etc. **also** work — the `/<alpha>` modifier is enabled on
> every token by `tokenColor()` in [tailwind.config.js](tailwind.config.js)
> — but prefer `toneClasses()` / the `-bg`/`-bd` tokens so the soft-pill
> stays identical everywhere.

Don't hand-write the recipe — call the helper in
[`src/lib/status.ts`](src/lib/status.ts):

```tsx
import { statusClasses, statusTone, toneClasses, toneText } from '@/lib/status';

// From a domain string (most common):
<Badge variant="outline" className={statusClasses(task.status)}>{task.status}</Badge>

// From a known tone:
<span className={toneClasses('danger')}>Overdue</span>

// Just the foreground (icons, dots, chart bars):
<AlertCircle className={toneText('warn')} />
```

`statusTone()` owns the string→tone mapping so the same status can't
render green in the table and amber in the badge. **Add new statuses to
that map — never pick a colour at the call-site.**

> ❌ `bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30`
> ✅ `toneClasses('ok')`

---

## 4. Typography

- **Font:** Geist Variable (`--font-sans`). Don't import another family.
- **Scale (what's actually used):**

| Class | Size | Role |
|---|---|---|
| `text-3xs` | 10px | Densest micro-labels — chip captions, axis hints, sparkline notes |
| `text-2xs` | 11px | Dense metadata — table sub-text, timestamps, secondary chips |
| `text-xs` | 12px | Dominant body / table cells / badges / metadata |
| `text-sm` | 14px | Default body, form labels, buttons |
| `text-base` | 16px | Emphasised body |
| `text-lg` / `text-xl` | 18/20px | Section / card titles |
| `text-2xl`+ | 24px+ | Page titles, big KPI numbers only |

- **No arbitrary sizes.** Use the scale — including `text-2xs` / `text-3xs`
  for sub-12px text. **Never** `text-[10px]` / `text-[11px]` / `text-[9px]`:
  those re-type the same value inconsistently. (`text-3xs` also covers the
  rare 8–9px cases.) Charts/SVG that need a numeric `fontSize` are the only
  exception.
- **Weights:** `font-medium` (default emphasis), `font-semibold`
  (headings), `font-bold` (KPI figures). Avoid `font-light`/`thin`.

### Roles — pick by role, not by eye

The scale above is the vocabulary; these are the **fixed combinations** for
each semantic role. A heading is the SAME size+weight on every page — don't
improvise (that's why some pages used to look heavier/lighter than others).

| Role | Classes | Where |
|---|---|---|
| Page title | `text-2xl font-bold` | top of page — **use `PageHeader`**, don't hand-roll |
| Section title | `text-lg font-semibold` | heading of a major section / card |
| Card / subsection title | `text-base font-semibold` | smaller card or sub-block heading |
| Section label (caps) | `text-xs font-medium uppercase tracking-wide text-muted-foreground` | small-caps group labels |
| Body / table cell | `text-sm` (`text-xs` in dense tables) | default running text |
| Caption / meta | `text-xs text-muted-foreground` | timestamps, secondary metadata |
| Code / ID | `font-mono text-xs` | **only** technical identifiers — record IDs, IPs, hashes, tokens, code snippets |

- **`font-mono` is for machine identifiers only.** Human-readable data —
  company codes, vehicle names, statuses, labels — uses the normal sans
  font (the default `DataGrid` cell). Don't `font-mono` a Company column
  on one page and leave it plain on another.
- **Same column, same render.** When the same logical column (Company,
  status, a count) appears on multiple pages, style it identically — lift
  the renderer to a shared helper rather than re-styling per page.

---

## 5. Spacing — 4px rhythm

Stay on Tailwind's 4px step scale. The house values, in order of
frequency: **gap** `1 / 1.5 / 2 / 3`; **padding-x** `2 / 2.5 / 3 / 4`;
**padding-y** `0.5 / 1 / 1.5 / 2`. Card padding is the `<Card>`
primitive's, not a per-site choice: `compact` (p-3) · `default` (p-4)
· `panel` (p-6, for a centred box that IS the page — auth, blockers,
success screens) · `none` (children own their edges). Radius is not a
variant — see §6.

- **No arbitrary spacing** (`p-[13px]`, `h-[42px]`) for layout. If a
  value isn't on the scale, it's almost always wrong. (Exceptions:
  pixel-exact needs like map overlays, fixed media dimensions.)
- **Keep writing `p-4`.** Every step on this scale is already multiplied
  by the user's Size setting — see §5.1. (This bullet used to point at
  `--spacing-card` / `--spacing-row`; those tokens were read by nothing,
  which is why the old Density picker moved zero pixels. They are gone.)

### 5.1 Size — the four multipliers ⭐

Every length Tailwind emits is `calc(step × var(--size-axis, 1))`. The
user's Size control writes those variables; nothing else changes. **You
keep writing `p-4`, `h-8`, `text-sm` exactly as before** — the class name
is the pointer, the multiplier is applied for you.

| Axis | Rides on | What it moves |
|---|---|---|
| `--size-text` | `fontSize`, `lineHeight` | type and its line box |
| `--size-control` | `w`/`h`/`size`/`min`/`max` ≤ 3rem | things a finger or cursor aims at |
| `--size-layout` | `padding`, `margin`, `gap`, `space`, `inset`, `translate`, `scroll*`, and dimensions 3–6rem | breathing room, and fixed content columns |
| `--size-panel` | dimensions > 6rem, `max-w-*` | menus, drawers, dialogs, panels |

The assignment tables are generated in
[`tailwind.config.js`](tailwind.config.js) from Tailwind's own defaults —
the axis rule is written once there, not as ~600 hand-maintained
formulas.

**Rules:**

- ❌ **Never extend `spacing`.** It is the shared default every dimension
  key derives from, so extending it moves padding, gap, margin, width,
  height and size at once and collapses the four axes into one. Extend
  the derived keys instead. (Verified: extending `padding` moves `p-*`
  alone and leaves `gap-3` / `w-3` / `h-3` untouched.)
- ❌ **No arbitrary lengths** (`h-[220px]`, `p-[3px]`). They are the one
  thing the multipliers cannot reach — an arbitrary value is a promise
  that the element will never follow the user's setting.
- ✅ **`min-h-tap` / `min-w-tap` — the one sanctioned non-scaling step.**
  A pointer target may not shrink below **24×24 CSS px** (WCAG 2.5.8 AA),
  and a floor expressed on the Size ladder is not a floor: it shrinks with
  everything else and stops protecting exactly when it is needed. Measured
  — the house `p-1 -m-1` invisible-padding idiom gives 24px at 1× and only
  **22px at 0.75×**, and `min-h-6` compiles to
  `calc(1.5rem * var(--size-control, 1))`. So every interactive control
  carries `min-h-tap` (plus `min-w-tap` when it is square or icon-only).
  It costs nothing at the default multiplier — the minimum equals the
  height already declared beside it — and it is what lets the Size floor
  drop below 100% at all. Do **not** spell it `min-h-[24px]`: a named step
  is greppable, and an arbitrary value silently emits no rule at all if
  the scanner never sees the literal.
- ⚠️ **Never subtract the shell frame in a `calc(100vh − Nrem)`.** That
  figure encodes today's padding, so it is wrong the moment Size moves —
  and two of the three that existed were already wrong at 1×. Use
  `h-full`, or `flex-1 min-h-0` inside a flex column.
- ⚠️ **A pixel constant standing in for a rendered height is now a bug.**
  `DrillDialog`'s sticky-header reservation is the worked example: it was
  `30`, justified by "one line of text at a fixed size", and that
  justification no longer holds. It is CSS now, riding the same two axes
  as the header it reserves for.
- **Per-component sizing is free.** `--size-*` inherits, so setting one
  on any wrapper scales everything inside it — no context, no props.
- **Regions are a THIRD factor, applied at the point of use.** Every
  length is `calc(step × var(--size-<axis>, 1) × var(--size-region, 1))`,
  and a surface claims a region by spreading `sizeRegion('tables')`
  ([`src/lib/sizeRegion.ts`](src/lib/sizeRegion.ts)) onto the root it
  already renders. Measured: unset costs nothing (identical boxes inside
  and out); global 1.2 × region 1.4 gives 26.88px from a 16px step, i.e.
  it MULTIPLIES — the nested-fallback form
  `var(--region, var(--global))` would have replaced the global and
  silently thrown 1.2 away. `min-h-tap` still measures 24.0px inside a
  region shrunk to 0.85.

  It is a style, not a wrapper component, because the surfaces that own
  a region are already flex parents and scroll containers, and slipping
  a `<div>` in between is how a layout loses `flex-1` or `min-h-0`.

  Nesting REPLACES rather than compounds — an overlay opened from inside
  the assistant renders at the overlay scale, not at both. A region names
  where you are, and you are in one place at a time.
- **The floor is 0.85, and it is a TYPE limit, not a target limit.** It
  was 1.0 while pointer targets had no floor of their own; 193 of them
  were measured under 24px in Chrome and given `min-h-tap`, which does
  not scale, so hit areas now hold all the way down. What stops the
  slider at 0.85 is legibility: `text-3xs` (10px) renders 8.5px there,
  and Geist's stem is one device pixel at 11.63px, so below that the
  dominant `text-xs` stops landing on whole pixels at DPR 1. Going lower
  needs a floor on the small type steps first. The clamp is written
  twice, in `preferences/registry.ts` and in the pre-paint script;
  `themeBoot.test.ts` asserts the two agree.

---

## 6. Radius

One variable, `--radius` (default `0.625rem`), drives everything via the
`rounded-*` scale. The theme picker's Corners preset (sharp / default /
pill) reshapes the whole UI from it.

- Use `rounded`, `rounded-md`, `rounded-lg`, `rounded-xl` — they all
  track `--radius`. `rounded-lg` is the card default.
- `rounded-full` (dots, avatars, pills) and `rounded-none` are shapes,
  not softness — leave them.
- **Never** hardcode `rounded-[10px]` or use `rounded-4xl` (it ignores
  the user's Corners setting — see the StatusBadge fix for why).
- **JS-drawn geometry** (SVG paths, canvas arcs) is bound by the same
  rule: a radius baked into a path number is invisible to the Corners
  preset. Read the live token —
  `getComputedStyle(document.documentElement).getPropertyValue('--radius')`
  — and recompute when `<html>`'s theme attributes change
  (MutationObserver; a CSS-var change alone never re-renders React).
  Reference implementation: `SegmentTab` in
  [`components/DataGrid.tsx`](src/components/DataGrid.tsx).
- **No radius caps on small variants.** Upstream shadcn ships `xs`/`sm`
  button and select sizes with `rounded-[min(var(--radius-md),10px)]`
  — a cap that silently ignores the Pill preset and makes a small
  button rounder than its neighbouring select. We removed those caps
  from `ui/button.tsx` / `ui/select.tsx` (all sizes now track
  `--radius`); don't reintroduce them when refreshing primitives from
  upstream.

---

## 7. Components

Build from the canonical primitives in
[`src/components/ui/`](src/components/ui/) (shadcn / base-ui). Don't
re-implement a button, badge, dialog, table, etc.

**Button** ([`ui/button.tsx`](src/components/ui/button.tsx)) — variants:
`default · outline · secondary · ghost · destructive · link`; sizes:
`xs · sm · default · lg · icon*`. Pick a variant; don't restyle with
ad-hoc classes.

**Badge** ([`ui/badge.tsx`](src/components/ui/badge.tsx)) — variants:
`default · secondary · destructive · outline · ghost · link`. For a
*status* badge use `variant="outline"` + `statusClasses()` (see
[`StatusBadge.tsx`](src/components/StatusBadge.tsx) as the reference).

**Shell** ([`src/components/shell/`](src/components/shell/)) — page
scaffolding: `PageHeader`, `KpiCard`, `EmptyState`, `ErrorState`,
`LoadingSkeleton`, `FilterBar`, `Breadcrumb`, toasts. Use these for
headers/empty/error/loading states — don't roll your own.

### View controls vs actions — separate the classes ⭐

A control that changes **what you are looking at** (a date range, a
scope switch, a mode) must not sit in the same visual group, at the same
weight, as controls that **do something** (create, book, delete). Both
render as bordered buttons in the header, so when they run together the
only way to tell a filter from an action is to read all of them —
and the filter is the one you touch most.

Put the view control first, then a divider
(`<span aria-hidden className="h-5 w-px bg-border mx-1" />`), then the
actions. Where a grid owns the constraint (search, column filters, sort)
it already renders its own removable chips — this rule is for the
PAGE-level constraints a grid cannot see. `features/loads/Loads.tsx` is
the worked example.

A view control also has a wider audience than an action: reading a
narrower slice is not a management act, so it renders for every viewer
even when the actions beside it are permission-gated.

### Empty states name the constraint that emptied them ⭐

"No loads in this status." is FALSE whenever a date window, scope, or
page-level filter is also narrowing the view — there may be forty, one
widening away. An empty state that states only the obvious condition
sends someone hunting an old record away believing it is gone.

Say what is filtering, and name the remedy:

```tsx
emptyMessage={rangeDays > 0
  ? 'No loads in this status in the selected date range. Widen the range to see older loads.'
  : 'No loads in this status.'}
```

Reports does the same for its own window ("Try a different tab or widen
the date range"). This applies to every constraint the grid cannot
render as a chip, because those are exactly the ones the reader cannot
see.

### Control & overlay sizing

Sizes are scales, exactly like colours are tokens — a control or overlay
picks a step, never invents a value.

- **Control heights** come from the Button primitive's ladder and apply to
  every interactive control (hand-rolled included):
  `h-7` (sm / dense toolbars) · **`h-8` (default)** · `h-9` (lg / hero
  actions). Square icon-buttons are the same steps: `size-7 · size-8 ·
  size-9`. `h-6` is reserved for micro-chips inside dense rows; `h-10+`
  for hero search fields only. If your control isn't on the ladder, use
  the Button primitive instead of styling a new one.
- **Menus & popovers**: `w-44` (compact action menu) · `w-56` (standard
  menu) · `w-64` (menu with descriptions) · `w-80` (list panel — history,
  notifications). Don't invent in-between widths.
- **Dialogs**: pick a `max-w-*` step — `max-w-lg` (S · confirm/simple
  form) · `max-w-xl` (M · standard form) · `max-w-2xl` (L · wide editor).
  Legacy `w-[480px]`-style dialogs migrate to the nearest step as you
  touch them (same convention as the `title=` migration).
- **Right-docked form drawers** (`border-l` slide-overs): always
  `w-full max-w-md` (S) · `max-w-lg` (M) · `max-w-xl` (L). The `w-full`
  is load-bearing — a fixed `w-[520px]` drawer overflows a phone
  viewport; `w-full` + the cap degrades to full-screen on mobile.
  (The assistant panel is chrome, not a form drawer — it has its own
  resizable width.)

### Layering — the z-index ladder

One ladder, low to high. A new `z-` value outside it is a stacking bug
waiting to happen (tooltip under modal, dropdown under panel):

| Layer | Value | What lives here |
|---|---|---|
| Content | `z-0 · z-10 · z-20` | within-page stacking (pinned cells, hover chrome) |
| Sticky chrome | `z-30` | sticky table headers, filter bars |
| App panels | `z-40` | slide-overs (assistant panel), drawers, launcher |
| Floating UI | `z-50` | menus, popovers, dialogs, tooltips, toasts |
| Above-dialog | `z-[60]` | command palette, media lightbox — the ONE sanctioned arbitrary |
| App blocker | `z-[100]` | the maintenance overlay only — beats everything |

**Maps are exempt where they must match Leaflet's internal pane values**
(`z-[400]`/`z-[500]`/`z-[650]`/`z-[1000]` etc. inside `live-map`/
`geofences` components) — those numbers are Leaflet's, not ours; keep
them next to a comment saying so.

### Icons

- **Library:** [`lucide-react`](https://lucide.dev) only. Don't add a
  second icon set or use inline `<svg>` / emoji as a UI icon (emoji are
  fine in *data* — POI markers, etc.).
- **Size = a standard step.** Pick from **`12 · 14 · 16 · 18 · 20 · 24`**
  (px), matched to the adjacent text:

| Icon | Pairs with | Use |
|---|---|---|
| `12` | `text-2xs`/`xs` | dense inline (table cells, chips) |
| `14` | `text-sm` | default inline (buttons, list rows) |
| `16` | `text-sm`/`base` | standalone / toolbar |
| `18`–`20` | `text-lg`/`xl` | section headers, emphasis |
| `24` | hero | empty-states, big callouts |

- **Colour** comes from a token — `text-muted-foreground`, `toneText('warn')`,
  `text-primary` — never a raw palette class.
- **Convention: the CLASS, never the prop.** `<Plus className="size-4" />`,
  not `<Plus size={16} />`. The two used to be interchangeable and are
  not any more: lucide's `size` prop writes `width`/`height` ATTRIBUTES
  on the `<svg>`, which no multiplier can reach — measured, a
  `size={16}` icon is 16px at 1× and still 16px at 1.5×, while every box
  and word around it grows. The class rides `--size-control` and comes
  out at 24px. The ladder in class form:

  | px | 10 | 12 | 14 | 16 | 18 | 20 | 24 |
  |---|---|---|---|---|---|---|---|
  | class | `size-2.5` | `size-3` | `size-3.5` | `size-4` | `size-4.5` | `size-5` | `size-6` |

  `size-4.5` is declared in `tailwind.config.js` because 18 is a
  sanctioned icon step that Tailwind's spacing ladder skips (it jumps
  16 → 20). It is added to the `size` key only — never to `spacing`,
  which would fuse the four axes.

  **Inside a `<Button>`, write no size at all.** The button's variant
  already sets its icon size (`[&_svg:not([class*='size-'])]:size-3.5`
  on `sm`, and so on), and `tailwind-merge` makes the variant win over
  the base — so an explicit class there opts the icon OUT of the
  button's own vocabulary. Measured: an icon in `<Button size="sm">`
  renders 14px whatever the prop said.

  A wrapper that takes a numeric `size` from ITS callers (`InfoTip`,
  `PoiIcon`, `EventIcon`) cannot know the number until runtime — those
  translate through `iconSizeClass()` in
  [`src/lib/iconSize.ts`](src/lib/iconSize.ts), the single place that
  mapping lives. **Don't** invent in-between sizes (`size-[11px]`,
  `size={13}`).

---

## 8. Charts & maps


**Scope: any colour consumer CSS cannot reach** — charts, map markers,
canvas, and 3D materials. All of them take tokens through a helper or a
config constant, never an inline literal. The one sanctioned literal is
a value that is not a colour at all: `new THREE.Color('#000000')` as a
"no emissive" lerp target in `AssemblyNode`. Mark such a site with a
comment saying why — an unmarked literal is indistinguishable from a
forgotten one.
These need literal colour strings (Recharts `fill`, Leaflet markers) —
they can't take Tailwind classes. **Still don't hardcode hex:**

- **Charts:** use `chartColor(n)` from `lib/status.ts`, which returns the
  `--chart-1..5` tokens. For status-coloured bars use `var(--ok)` etc.
- **Maps:** marker/route colours belong in a shared palette constant
  (e.g. [`config/poiLayers.ts`](src/config/poiLayers.ts)), referenced —
  not re-typed per layer. Keep all map hex in config, never inline in a
  `<...Layer>` component.

---

## 9. The other two apps

| App | Language | Why separate |
|---|---|---|
| `dashboard` | This doc — shadcn + oklch tokens + theme picker | Customer-facing, themeable |
| `system_dashboard` | Slate-scale console, single accent, no theme picker | Internal tool for ~3 operators; deliberately reads as a "console" |
| `miniapp` | Telegram-native (bridges `tg.themeParams`) | Must match the host Telegram theme |

They are intentionally distinct and do **not** share this token set.
This doc governs `dashboard` only.

---

## 10. Performance budgets

Speed limits for user-facing surfaces — adopted 2026-08-21 from the
instrumented Dispatch-KPI audits, owner-approved.  A change that
breaks one of these does not ship; an audit argues against these
numbers, never from scratch.

| SLI | Budget | Note |
|---|---|---|
| INP, any gesture | **< 200 ms @ 4× CPU throttle** | the human "frozen" line, on a cheap-laptop simulation |
| Gesture settle (click → last work) | **< 300 ms @ 1×, < 800 ms @ 4×** | heavy boards assemble AFTER the click — INP alone under-describes them |
| Long tasks during a gesture | **none > 50 ms** | input-blocking |
| CLS on load | **≤ 0.1** | reserve space for late data — nothing may shove the page |
| DOM nodes, board-class views | **≤ 2,500 at any fleet size** | volume is the multiplier behind every other cost |
| Time to primary data on screen | **≤ 2,000 ms** | |
| Server share of the load path | tracked, **≤ 35 %** | frontend work cannot fix a backend-bound load |
| Frame budget | `1000 / refresh-rate` ms | 10 ms on a 100 Hz display — never assume 16.7 |

**How to measure — the METHOD lives in exactly one place:** the
`ux-audit-performance-interaction` skill
([.claude/skills/ux-audit-performance-interaction/SKILL.md](../../.claude/skills/ux-audit-performance-interaction/SKILL.md))
owns the whole protocol — the DevTools passes, ×3-with-spread, the
4×-throttle doctrine, the seeded-rig pattern, never-a-real-account,
the disposable-measurement and workspace-isolation rules, and the
bundled universal runner (`tools/measure.mjs`).  This section holds
only THIS project's numbers — the values layer the skill reads in its
Step 0b.  This project's kept harness:
[abc-lab/skills/ux-audit-performance-interaction/perf-rig/](../../abc-lab/skills/ux-audit-performance-interaction/perf-rig/README.md) — the seeded
12-dispatcher / 69-truck board for A/B runs.

## 11. Hard rules (the enforcement checklist)

- ❌ No `#hex` in `.tsx` components (charts/maps → config or `chartColor()`).
- ❌ No raw Tailwind palette for meaning: `text-red-500`, `bg-green-100`,
  `border-amber-500` → use `toneClasses()` / status tokens.
- ❌ No arbitrary spacing/size (`p-[13px]`, `h-[42px]`) for layout.
- ❌ No arbitrary text size (`text-[10px]`, `text-[11px]`) → use the scale,
  incl. `text-2xs` / `text-3xs`.
- ❌ No off-step icon sizes (`size={11}`, `size={13}`, `size={22}`) → use
  `12 · 14 · 16 · 18 · 20 · 24`; lucide-react only.
- ❌ No improvised heading styles — use the §4 **role** combos (page title
  `text-2xl font-bold`, section title `text-lg font-semibold`, card title
  `text-base font-semibold`). A heading is the same size+weight everywhere.
- ❌ No `font-mono` on human-readable data (company codes, names, statuses)
  — mono is for machine identifiers (IDs/IPs/hashes/code) only.
- ❌ No hardcoded radius (`rounded-[10px]`, `rounded-4xl`).
- ❌ No re-implemented primitives — use `ui/*` and `shell/*`.
- ❌ No off-ladder control heights — interactive controls sit on
  `h-7 · h-8 · h-9` (`size-7/8/9` for icon-buttons); menus/popovers on
  `w-44 · w-56 · w-64 · w-80`; new dialogs on `max-w-lg/xl/2xl` (§7).
- ❌ No z-index outside the §7 ladder (`0–20` content · `30` sticky ·
  `40` panels · `50` floating UI · `z-[60]` above-dialog · `z-[100]`
  maintenance blocker). Map components matching Leaflet pane values are
  the documented exception — comment them.
- ❌ No **inherited** text colour on a surface — declare `text-foreground`
  / the matching `*-foreground`. `bg-<x>` and `text-<x>-foreground` travel
  together (see §2 "Declare colour"). A control with a `bg`/`placeholder`
  but no value colour is the classic light-theme-invisible bug.
- ❌ No raw palette in **`index.html`** either (it's in scope) — and there is
  now **no allowed exception**: the always-dark splash `bg` that used to be
  one is gone, replaced by the pre-paint theme stamp (§2). Body carries only
  `text-foreground`. A raw near-white default there is what made input text
  invisible on light; the splash literal is what left light users on a dark
  canvas.
- ✅ Colour comes from a token; status from a tone; spacing from the 4px
  scale; radius from `--radius`; type from the Geist scale (incl. 2xs/3xs)
  at a §4 role combo; icons from lucide at a standard step.
- ✅ Verified in **both** themes — light and dark — before shipping a new
  surface. Low contrast in one theme is a bug even if the other looks fine.

When unsure, grep for an existing screen that does the same thing and
copy its tokens.

### Which of these are GUARDED

A rule that lives only in this file decays — twice in one session an
audit found violations of rules written here for months. These now fail
`npm test`, in `src/components/ui/chrome.test.ts` (plus
`scrolling/backdrops.test.ts` for hand-rolled modals):

| Rule | Guard |
|---|---|
| 24px pointer floor (§5.1) | computes each control's height from its classes; **validated against 728 elements measured in Chrome — 428/428 verdicts matched**, and it abstains on the 116 whose line-height is inherited |
| never extend `spacing` (§5.1) | reads `tailwind.config.js` |
| no `calc(100vh − Nrem)` (§5.1) | code lines only, comments excluded |
| no arbitrary px/rem length (§5.1, §11) | viewport units and `var()` deliberately allowed |
| no raw palette for meaning (§11) | prose mentions excluded |
| no emoji as icons (§11) | explicit dingbat list — an arrow in "A → B" is typography, not an icon |
| no native `title=` (§11) | `<iframe title>` exempt: there it is the required accessible name |
| per-user state via the registry | allowlist mirrors `preferences/CLAUDE.md`'s exception table |
| don't hand-roll a Button variant (§7) | a raw `<button>` wearing a variant's own dimensions |
| don't override a Button variant (§7) | dimensions only — a call site may set colour |

Three carry NAMED DEBT lists for migrations older than the guards
(`title=`, arbitrary lengths, one Button override). A separate test
fails if an entry on any list stops offending, so a list cannot outlive
its reason.

**Not guarded, and worth knowing:** heading ROLE combos (§4), the
`font-mono` rule, icon step values, and whether two sibling surfaces
agree on a legal value. The last is not a guard's job at all — a guard
checks that a value is legal; only a primitive can make siblings
agree.
