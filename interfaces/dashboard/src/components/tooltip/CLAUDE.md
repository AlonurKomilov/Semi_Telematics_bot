# components/tooltip — hover-info & explanation rules

Full rules for the tooltip family. The main dashboard file
[interfaces/dashboard/CLAUDE.md](../../../CLAUDE.md) carries the short
consumer pointer; this file holds the detail so the main file stays lean.
`components/tooltip/` is the SSOT for hover labels, data-age indicators, and
learn-once field explanations — never native `title=`.

## The family (three members, one look)

- `<Tip label="…">` — plain hover label for the 90% case; the drop-in
  replacement for native `title=`. Hover + cursor-anchored.
- `<Freshness ts={…} sla={…}>` — data-age indicator (quiet-by-default
  dot). **The hour is a default, not the policy**: pass `sla` (minutes —
  the row's `sla_min`, which is the dataset's declared
  `freshness_sla_min`) and the dot fires at THAT age, the same age the
  backend reader falls back and the watchdog pages. One number in three
  places, never a fourth. Vehicle rows carry `sla_min` from `_simplify`;
  a value without a declared tolerance keeps the hour.
- `<InfoTip>` — learn-once field explanation behind a muted ⓘ.

One `TooltipProvider` is mounted in `main.tsx` — never add per-instance
providers.

## Never native `title=`

Native `title=` renders the browser's unthemed, ~1s-delayed tooltip and is
invisible on touch — an ESLint rule warns on it on DOM elements; migrate
legacy usages as you touch files. Keep `aria-label` on icon-only controls
(the tooltip is a sighted-hover affordance, NOT the accessible name).

## Learn-once explanations = `<InfoTip>`, not always-visible text

Helper text a user reads twice and then scrolls past ("saved to the history
with your next action") collapses behind the muted ⓘ from
[`InfoTip`](InfoTip.tsx) next to the field label. Page descriptions collapse
the same way — the `PageHeader description` prop renders as ⓘ beside the
title (one change in the shell converts every page; keep writing the prop).

**Keep VISIBLE** (do NOT collapse into an InfoTip): dynamic/state feedback
("No odometer telemetry…", "Saving…") and one-time-user forms (the public
apply form — its users never get a second visit, so visible help is correct
there).

**Icon semantics** — keep honest: ⓘ = explanation · (?) = how-to · (!) =
warnings ONLY.

**Behavior split**: `InfoTip` is a TOGGLETIP — opens on CLICK, anchored to
the ⓘ itself, closes on outside-click / Esc (works on touch; multi-sentence
bubbles survive mouse drift). `Tip` / `Freshness` stay hover +
cursor-anchored. Migrate legacy helper texts as you touch files.
