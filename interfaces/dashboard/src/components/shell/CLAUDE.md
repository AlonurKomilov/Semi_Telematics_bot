# components/shell — app-shell primitive rules

Full rules for shell primitives. The main dashboard file
[interfaces/dashboard/CLAUDE.md](../../../CLAUDE.md) carries the short
consumer pointer; this file holds the detail so the main file stays lean.
(Most shell pieces — `PageHeader`, `EmptyState`, `ErrorState`,
`CardSkeleton`/`TableSkeleton` — are compose-and-go: reach for them instead
of re-implementing page chrome / empty / error / loading states. The one
that needs a real rule-set is the time-window control below.)

## Time windows = `DateRangePresets`, always

[`DateRangePresets.tsx`](DateRangePresets.tsx) is the SSOT for "pick a time
window" (same idea as DataGrid for tables): never hand-roll a days dropdown,
a chip row, or a numeric days input. Two forms, one `days` contract:

- `variant="dropdown"` (default) — toolbars / forms; presets + a custom
  calendar.
- `variant="segments"` — inline chip row for chart / section headers.

Pass `options` for page-specific windows, `maxDays` when the backend accepts
more than 90, `disabled` while generating, `isFetching` for the spinner.

**END dates are per-page and FAIL-CLOSED**: pass `onApplyRange` + `end` ONLY
when the page's backend honors an explicit `end=` param (worked example:
Safety Events) — otherwise the calendar stays start-only and the range
honestly ends today.

**NOT this component**: config values that merely happen to be day counts
(link expiry, rule periods, service intervals, backfill action menus) —
those stay form inputs.
