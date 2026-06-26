import { useMemo, useState } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Search, X, Check } from 'lucide-react';
import { cn } from '../lib/utils';

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
 *   - AND across columns (DataTable composes via @tanstack columnFilters)
 *   - Empty selection = no filter on this column (same as before opening)
 *   - Live-updates as boxes tick (no Apply button — matches Notion / Linear UX)
 *
 * Why not use ``ui/dropdown-menu``: the Menu primitive's items dismiss
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

interface ColumnFilterMenuProps {
  /** Display name shown above the option list. */
  label: string;
  /** All available options from the column (uniques computed by caller).
   *  Each option carries both the match-value and the display label. */
  options: ColumnFilterOption[];
  /** Per-value row count, so operators see "CFT (12)" not just "CFT". */
  counts: Record<string, number>;
  /** Currently selected values.  Empty array = no filter. */
  value: string[];
  /** Called on every tick / clear — caller updates table state. */
  onChange: (next: string[]) => void;
  /** Trigger element — usually the column header label.  Receives the
   *  same hover / focus treatment as a regular header. */
  children: React.ReactNode;
}

export default function ColumnFilterMenu({
  label, options, counts, value, onChange, children,
}: ColumnFilterMenuProps) {
  const [open, setOpen] = useState(false);
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
      <PopoverPrimitive.Trigger
        // Forward as a button so keyboard users land on the trigger
        // via Tab + can open with Enter / Space (the underlying
        // base-ui Popover handles aria-haspopup + expanded for us).
        render={(props) => (
          <button
            type="button"
            {...props}
            // stopPropagation so clicking the label doesn't bubble to
            // a wrapping TableHead onClick (which the legacy non-
            // filterable header used for sort).  Sort is its own
            // button next to the label, not a parent gesture.
            onClick={(e) => {
              e.stopPropagation();
              props.onClick?.(e);
            }}
          >
            {children}
          </button>
        )}
      />
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
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
                  size={12}
                  aria-hidden="true"
                  className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
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
                      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-accent text-left"
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
                        {sel && <Check size={10} className="text-primary-foreground" aria-hidden="true" />}
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
                  <X size={11} aria-hidden="true" />
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
