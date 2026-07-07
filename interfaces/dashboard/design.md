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
3. **Theme-driven.** Colour, radius, and density all flow from CSS
   variables the user can re-skin from the theme picker. Hardcoding any
   of them breaks that switch silently.
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

Why this matters (a real bug this rule exists to prevent): `<body>` carries
an always-dark splash background, so its text default was historically a
raw near-white. Form controls (`Input`, `Textarea`) declared a `bg` and a
`placeholder:` colour but **no value colour** — so the typed text inherited
near-white. On the **dark** theme (dark field) it was visible; on the
**light** theme (white field) it was white-on-white — invisible. The field
looked empty even though text was there.

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
**padding-y** `0.5 / 1 / 1.5 / 2`. Card padding `p-3`/`p-4`.

- **No arbitrary spacing** (`p-[13px]`, `h-[42px]`) for layout. If a
  value isn't on the scale, it's almost always wrong. (Exceptions:
  pixel-exact needs like map overlays, fixed media dimensions.)
- Density is theme-driven: prefer `--spacing-card` / `--spacing-row`
  over fixed paddings on re-skinnable surfaces.

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
- **Convention:** `size={16}` (lucide prop) or `size-4` (Tailwind) — both
  fine; just stay on the step values above. **Don't** invent in-between
  sizes (`size={11}`, `size={13}`, `size={22}`).

---

## 8. Charts & maps

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

## 10. Hard rules (the enforcement checklist)

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
- ❌ No **inherited** text colour on a surface — declare `text-foreground`
  / the matching `*-foreground`. `bg-<x>` and `text-<x>-foreground` travel
  together (see §2 "Declare colour"). A control with a `bg`/`placeholder`
  but no value colour is the classic light-theme-invisible bug.
- ❌ No raw palette in **`index.html`** either (it's in scope). The body's
  always-dark splash `bg` is the one allowed literal — its text is a token
  (`text-foreground`). A raw near-white default there is what made input
  text invisible on light.
- ✅ Colour comes from a token; status from a tone; spacing from the 4px
  scale; radius from `--radius`; type from the Geist scale (incl. 2xs/3xs)
  at a §4 role combo; icons from lucide at a standard step.
- ✅ Verified in **both** themes — light and dark — before shipping a new
  surface. Low contrast in one theme is a bug even if the other looks fine.

When unsure, grep for an existing screen that does the same thing and
copy its tokens.
