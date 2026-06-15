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
- **Charts/maps** can't use classes: charts → `chartColor(n)` (the
  `--chart-1..5` tokens); map hex → a shared config constant, never inline.

This file governs `interfaces/dashboard` only. The `system_dashboard`
(operator console) and `miniapp` (Telegram) are separate design languages
by intent — see design.md §9.
