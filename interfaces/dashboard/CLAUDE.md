# Dashboard UI rules

**Before writing or changing any UI in this app, follow the design system
in [design.md](design.md).** It is the single source of truth. Key rules:

- **Colour = token, never literal.** No `#hex` in components. No raw
  Tailwind palette (`text-green-500`, `bg-amber-100`). Use the semantic
  tokens (`bg-card`, `text-muted-foreground`, `bg-primary`, …).
- **Status/severity = a tone, via the helper.** Import from
  [`src/lib/status.ts`](src/lib/status.ts): `statusClasses(status)`,
  `toneClasses('danger')`, `toneText('warn')`. Add new statuses to the
  `statusTone` map there — never pick a colour at the call-site. The four
  tones are `ok / warn / danger / info` (+ `neutral`).
- **Spacing = 4px scale.** Use `gap-2`, `px-3`, `py-1.5`, etc. No
  arbitrary values (`p-[13px]`, `h-[42px]`) for layout.
- **Radius = `--radius`.** Use `rounded`, `rounded-md`, `rounded-lg`.
  Never `rounded-[10px]` or `rounded-4xl` (ignores the theme picker).
- **Type = Geist scale.** `text-xs`/`text-sm` body, `font-medium`/
  `font-semibold` emphasis.
- **Compose primitives.** Build from [`src/components/ui/`](src/components/ui/)
  and [`src/components/shell/`](src/components/shell/) — don't re-implement
  buttons, badges, dialogs, empty/error/loading states.
- **Charts/maps** can't use classes: charts → `chartColor(n)` (the
  `--chart-1..5` tokens); map hex → a shared config constant, never inline.

This file governs `interfaces/dashboard` only. The `system_dashboard`
(operator console) and `miniapp` (Telegram) are separate design languages
by intent — see design.md §9.
