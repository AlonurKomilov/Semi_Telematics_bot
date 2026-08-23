import { useMemo, useState } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Search, X, Check } from 'lucide-react';
import { cn } from '../../lib/utils';

/**
 * Per-column filter dropdown — opened from a plain-label header.
 *
 * UX shape:
 *   ┌──────────────────────────┐
 *   │ [🔍 Search…           ]  │  ← only shown when uniques > 8
 *   ├──────────────────────────┤
 *   │ ☑ CFT      (12)          │
 *   │ ☐ G1       (8)           │
 *   │ ☐ OSY      (5)           │
 *   │ ☐ PTG      (4)           │
 *   ├──────────────────────────┤
 *   │ Clear · 5 selected       │
 *   └──────────────────────────┘
 *
 * Selection semantics:
 *   - OR within column (any selected value matches)
 *   - AND across columns (DataGrid composes via @tanstack columnFilters)
 *   - Empty selection = no filter on this column (same as before opening)
 *   - Live-updates as boxes tick (no Apply button — matches Notion / Linear UX)
 *
 * Why not a Menu primitive here: its items dismiss
 * the menu on click, which breaks multi-select.  Popover gives us a
 * persistent surface where multiple ticks compose without closing.
 */

/** One filter option — value is what we match on (e.g. ``oil``),
 *  label is what the operator sees (e.g. ``Oil Change``).  Decoupled
 *  so badge-backed columns can show friendly names without changing
 *  the underlying match semantics. */
export interface ColumnFilterOption {
  value: string;
  label: string;
}

interface CommonProps {
  /** Display name shown above the option list. */
  label: string;
  /** Controlled open state — the column's 3-dot menu's "Filter…"
   *  item flips this true to open the popover.  The header has no
   *  inline trigger anymore (per UX: column label is just text +
   *  sort, filter lives behind the 3-dot menu). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Anchor element the popover positions against.  Usually the
   *  column header <th> so the popup appears just below the header
   *  the operator clicked through to.  Pass ``null`` and the
   *  positioner falls back to the document body. */
  anchor: HTMLElement | null;
}

type SelectProps = CommonProps & {
  mode?: 'select';
  /** All available options from the column (uniques computed by caller).
   *  Each option carries both the match-value and the display label. */
  options: ColumnFilterOption[];
  /** Per-value row count, so operators see "CFT (12)" not just "CFT". */
  counts: Record<string, number>;
  /** Currently selected values.  Empty array = no filter. */
  value: string[];
  /** Called on every tick / clear — caller updates table state. */
  onChange: (next: string[]) => void;
};

type RangeProps = CommonProps & {
  mode: 'range';
  /** ``[min, max]`` inclusive; ``null`` on either side = one-sided
   *  range ("≥ 50" / "≤ 200").  ``[null, null]`` = no filter. */
  value: [number | null, number | null];
  onChange: (next: [number | null, number | null]) => void;
  /** Data-driven bounds + optional unit / step.  Placeholder text on
   *  the Min/Max inputs shows the actual min/max in the data so the
   *  operator knows what range they're bounding. */
  bounds: { min: number; max: number; step: number; unit: string };
};

type DateRangeProps = CommonProps & {
  mode: 'date-range';
  /** ``[isoFrom, isoTo]`` (YYYY-MM-DD); ``null`` on either side = one-
   *  sided range.  ``[null, null]`` = no filter.  Upper bound is
   *  applied inclusive-to-end-of-day by the DataGrid filterFn. */
  value: [string | null, string | null];
  onChange: (next: [string | null, string | null]) => void;
  /** Earliest + latest date in the data (ISO YYYY-MM-DD).  Shown as
   *  placeholder + "Data range" hint so the operator knows the span
   *  they're bounding. */
  bounds: { min: string; max: string };
};

type ColumnFilterMenuProps = SelectProps | RangeProps | DateRangeProps;

export default function ColumnFilterMenu(props: ColumnFilterMenuProps) {
  if (props.mode === 'range') return <RangeFilter {...props} />;
  if (props.mode === 'date-range') return <DateRangeFilter {...props} />;
  return <SelectFilter {...props} />;
}

function SelectFilter({
  label, options, counts, value, onChange,
  open, onOpenChange, anchor,
}: SelectProps) {
  const setOpen = onOpenChange;
  const [search, setSearch] = useState('');
  const selectedSet = useMemo(() => new Set(value), [value]);

  // Sort: selected first, then by count desc, then alphabetically by
  // LABEL (the operator-visible text — matches what they see and the
  // typeahead semantics below).
  const sortedOptions = useMemo(() => {
    return [...options].sort((a, b) => {
      const aSel = selectedSet.has(a.value) ? 0 : 1;
      const bSel = selectedSet.has(b.value) ? 0 : 1;
      if (aSel !== bSel) return aSel - bSel;
      const cDiff = (counts[b.value] ?? 0) - (counts[a.value] ?? 0);
      if (cDiff !== 0) return cDiff;
      return a.label.localeCompare(b.label);
    });
  }, [options, counts, selectedSet]);

  // Search matches against BOTH label and value — operator might
  // type "oil" (label fragment) or the internal code if they know it.
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return sortedOptions;
    return sortedOptions.filter(o =>
      o.label.toLowerCase().includes(needle)
      || o.value.toLowerCase().includes(needle),
    );
  }, [sortedOptions, search]);

  const toggle = (val: string) => {
    if (selectedSet.has(val)) {
      onChange(value.filter(v => v !== val));
    } else {
      onChange([...value, val]);
    }
  };

  // Search input only when there are enough options to scroll past.
  // Threshold 8 chosen to match typical role lists (8 roles) — bigger
  // sets (companies, vehicles) get the search box.
  const showSearch = options.length > 8;

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor ?? undefined}
          align="start"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup
            className="w-56 bg-popover text-popover-foreground border border-border rounded-md shadow-lg overflow-hidden"
          >
            {/* Header — column label + selection count */}
            <div className="px-3 py-2 border-b border-border flex items-baseline justify-between">
              <span className="text-xs font-medium text-foreground">
                Filter {label}
              </span>
              {value.length > 0 && (
                <span className="text-2xs text-muted-foreground">
                  {value.length} selected
                </span>
              )}
            </div>

            {showSearch && (
              <div className="relative p-2 border-b border-border">
                <Search
                  aria-hidden="true"
                  className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none size-3"
                />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search values…"
                  autoFocus
                  className="w-full bg-muted text-xs text-foreground placeholder:text-muted-foreground rounded pl-7 pr-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
            )}

            {/* Option list */}
            <div className="max-h-64 overflow-y-auto py-1">
              {filtered.length === 0 ? (
                <div className="px-3 py-2 text-2xs text-muted-foreground italic">
                  No matches
                </div>
              ) : (
                filtered.map((opt) => {
                  const sel = selectedSet.has(opt.value);
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => toggle(opt.value)}
                      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-accent text-left min-h-tap"
                      title={opt.label}
                    >
                      <span
                        className={cn(
                          'shrink-0 w-3.5 h-3.5 rounded border flex items-center justify-center transition',
                          sel
                            ? 'bg-primary border-primary'
                            : 'border-border',
                        )}
                      >
                        {sel && <Check className="text-primary-foreground size-3" aria-hidden="true" />}
                      </span>
                      <span className="flex-1 truncate text-foreground">
                        {opt.label || <em className="text-muted-foreground">(empty)</em>}
                      </span>
                      <span className="shrink-0 text-2xs text-muted-foreground">
                        {counts[opt.value] ?? 0}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            {/* Footer — clear, only when at least one selected */}
            {value.length > 0 && (
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => onChange([])}
                  className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1"
                >
                  <X className="size-3" aria-hidden="true" />
                  Clear {label} filter
                </button>
              </div>
            )}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

/**
 * Range-filter variant — two numeric inputs (Min / Max) for
 * continuous columns like Fuel %, Odometer, Engine Hours.
 *
 *   ┌──────────────────────────┐
 *   │ Filter Odometer          │
 *   ├──────────────────────────┤
 *   │ Min  [  50000  ]  mi     │
 *   │ Max  [ 300000  ]  mi     │
 *   │      Range: 0 – 1,051,797 │
 *   ├──────────────────────────┤
 *   │ Clear Odometer filter    │
 *   └──────────────────────────┘
 *
 * Semantics: inclusive bounds ([min ≤ n ≤ max]); either side can be
 * blank for a one-sided filter ("≤ 100" or "≥ 50000"); blank/blank
 * clears.  Placeholder text carries the data-driven bounds so the
 * operator knows what range they're bounding.
 */
function RangeFilter({
  label, value, onChange, bounds,
  open, onOpenChange, anchor,
}: RangeProps) {
  const setOpen = onOpenChange;
  const [minStr, maxStr] = [
    value[0] == null ? '' : String(value[0]),
    value[1] == null ? '' : String(value[1]),
  ];
  const parse = (s: string): number | null => {
    if (s.trim() === '') return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  };
  const active = value[0] != null || value[1] != null;
  // Format big numbers with locale commas so "1,051,797" reads
  // instantly.  Small values (percentages) skip commas naturally.
  const fmt = (n: number) => n.toLocaleString();
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor ?? undefined}
          align="start"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup className="w-56 bg-popover text-popover-foreground border border-border rounded-md shadow-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-baseline justify-between">
              <span className="text-xs font-medium text-foreground">
                Filter {label}
              </span>
              {active && (
                <span className="text-2xs text-muted-foreground">active</span>
              )}
            </div>
            <div className="p-3 space-y-2">
              <label className="flex items-center gap-2 text-xs text-foreground">
                <span className="w-8 text-muted-foreground">Min</span>
                <input
                  type="number"
                  value={minStr}
                  step={bounds.step}
                  placeholder={fmt(bounds.min)}
                  onChange={(e) => onChange([parse(e.target.value), value[1]])}
                  className="flex-1 min-w-0 bg-muted text-foreground placeholder:text-muted-foreground rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                />
                {bounds.unit && (
                  <span className="w-6 text-muted-foreground text-2xs">
                    {bounds.unit}
                  </span>
                )}
              </label>
              <label className="flex items-center gap-2 text-xs text-foreground">
                <span className="w-8 text-muted-foreground">Max</span>
                <input
                  type="number"
                  value={maxStr}
                  step={bounds.step}
                  placeholder={fmt(bounds.max)}
                  onChange={(e) => onChange([value[0], parse(e.target.value)])}
                  className="flex-1 min-w-0 bg-muted text-foreground placeholder:text-muted-foreground rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                />
                {bounds.unit && (
                  <span className="w-6 text-muted-foreground text-2xs">
                    {bounds.unit}
                  </span>
                )}
              </label>
              <p className="text-2xs text-muted-foreground pt-1">
                Data range: {fmt(bounds.min)}{bounds.unit && ` ${bounds.unit}`}
                {' – '}
                {fmt(bounds.max)}{bounds.unit && ` ${bounds.unit}`}
              </p>
            </div>
            {active && (
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => onChange([null, null])}
                  className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1"
                >
                  <X className="size-3" aria-hidden="true" />
                  Clear {label} filter
                </button>
              </div>
            )}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

/**
 * Date-range filter — two native ``<input type="date">`` pickers
 * (From / To) for date/timestamp columns.  Same shape as RangeFilter
 * but with browser-native date pickers instead of numeric inputs, so
 * we don't need a date-picker library and the OS-native calendar UI
 * comes for free.
 *
 *   ┌──────────────────────────┐
 *   │ Filter Submitted  active │
 *   ├──────────────────────────┤
 *   │ From  [ 2025-01-01 ]     │
 *   │ To    [ 2025-12-31 ]     │
 *   │      Data range: 2024-05-01 – 2026-06-30 │
 *   ├──────────────────────────┤
 *   │ Clear Submitted filter   │
 *   └──────────────────────────┘
 *
 * Semantics: inclusive [from ≤ date ≤ to]; either side may be blank
 * for a one-sided filter ("after 2025-01-01" / "before 2025-12-31");
 * blank/blank clears.  The DataGrid filterFn extends the "To" bound
 * to end-of-day so a single-day filter keeps the whole day of data.
 */
function DateRangeFilter({
  label, value, onChange, bounds,
  open, onOpenChange, anchor,
}: DateRangeProps) {
  const setOpen = onOpenChange;
  const active = value[0] != null || value[1] != null;
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor ?? undefined}
          align="start"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup className="w-64 bg-popover text-popover-foreground border border-border rounded-md shadow-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-baseline justify-between">
              <span className="text-xs font-medium text-foreground">
                Filter {label}
              </span>
              {active && (
                <span className="text-2xs text-muted-foreground">active</span>
              )}
            </div>
            <div className="p-3 space-y-2">
              <label className="flex items-center gap-2 text-xs text-foreground">
                <span className="w-10 text-muted-foreground">From</span>
                <input
                  type="date"
                  value={value[0] ?? ''}
                  min={bounds.min || undefined}
                  max={bounds.max || undefined}
                  onChange={(e) => onChange([e.target.value || null, value[1]])}
                  className="flex-1 min-w-0 bg-muted text-foreground rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </label>
              <label className="flex items-center gap-2 text-xs text-foreground">
                <span className="w-10 text-muted-foreground">To</span>
                <input
                  type="date"
                  value={value[1] ?? ''}
                  min={bounds.min || undefined}
                  max={bounds.max || undefined}
                  onChange={(e) => onChange([value[0], e.target.value || null])}
                  className="flex-1 min-w-0 bg-muted text-foreground rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </label>
              {(bounds.min || bounds.max) && (
                <p className="text-2xs text-muted-foreground pt-1">
                  Data range: {bounds.min || '—'} – {bounds.max || '—'}
                </p>
              )}
            </div>
            {active && (
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => onChange([null, null])}
                  className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1"
                >
                  <X className="size-3" aria-hidden="true" />
                  Clear {label} filter
                </button>
              </div>
            )}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
