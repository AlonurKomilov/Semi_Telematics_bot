import { useMemo, useState } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Check, RotateCcw, Columns3, Search } from 'lucide-react';
import { cn } from '../../lib/utils';

/**
 * "Manage columns" popover — bulk visibility editor.
 *
 *   ┌──────────────────────────────┐
 *   │ Columns                      │
 *   ├──────────────────────────────┤
 *   │ ☑ Vehicle                    │
 *   │ ☑ Company                    │
 *   │ ☐ Priority                   │  ← unchecked = hidden
 *   │ ☑ Type                       │
 *   │ ☑ Description                │
 *   │ ☑ Status                     │
 *   ├──────────────────────────────┤
 *   │ ↻ Reset                      │
 *   └──────────────────────────────┘
 *
 * Renders nothing on its own — the parent provides a trigger node via
 * ``children``.  The DataGrid invokes it from the 3-dot menu's
 * "Manage columns…" item.
 *
 * "Reset" clears the table's local visibility + order state so the
 * next render falls back to the column config defaults.
 */

interface ManageColumnsOption {
  id: string;
  label: string;
  /** Hide-disabled options grey out and can't be unchecked (e.g. the
   *  primary key column where hiding it would leave the row useless). */
  alwaysVisible?: boolean;
  /** Optional bucket heading. A grid whose columns come from a large
   *  template (a carrier profile has 73 fields) is unusable as one flat
   *  list — the popover becomes a scroll pit and finding "Pet Policy"
   *  means reading everything. Options keep their given order within a
   *  group, and groups appear in first-seen order. */
  group?: string;
}

interface ManageColumnsMenuProps {
  options: ManageColumnsOption[];
  /** Map of column id → visible.  Missing = visible (default). */
  visibility: Record<string, boolean>;
  onToggle: (id: string) => void;
  onReset: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Optional anchor — when omitted, popover positions relative to the
   *  document body (caller should provide an external trigger).  When
   *  set, popover anchors to this element. */
  anchor?: HTMLElement | null;
}

// Past this many options a flat list stops being scannable and the
// popover earns a filter box.
const SEARCH_THRESHOLD = 12;

export default function ManageColumnsMenu({
  options, visibility, onToggle, onReset, open, onOpenChange, anchor,
}: ManageColumnsMenuProps) {
  const hideableVisibleCount = options.filter(
    (o) => !o.alwaysVisible && visibility[o.id] !== false,
  ).length;
  const [query, setQuery] = useState('');
  const showSearch = options.length > SEARCH_THRESHOLD;

  // Filter first, then bucket — so a search inside a grouped list keeps
  // its headings and you can still see WHERE a match lives.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hits = q
      ? options.filter((o) => o.label.toLowerCase().includes(q)
        || (o.group ?? '').toLowerCase().includes(q))
      : options;
    const out: { name: string; items: ManageColumnsOption[] }[] = [];
    for (const o of hits) {
      const name = o.group ?? '';
      const last = out[out.length - 1];
      if (last && last.name === name) last.items.push(o);
      else out.push({ name, items: [o] });
    }
    return out;
  }, [options, query]);

  const visibleCount = options.filter((o) => visibility[o.id] !== false).length;
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor ?? undefined}
          align="end"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup className="w-56 bg-popover text-popover-foreground border border-border rounded-md shadow-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <Columns3 className="text-muted-foreground size-3.5" />
              <span className="text-xs font-medium text-foreground">Columns</span>
              <span className="ml-auto text-2xs text-muted-foreground tabular-nums">
                {visibleCount} of {options.length}
              </span>
            </div>
            {showSearch && (
              <div className="px-2 py-1.5 border-b border-border flex items-center gap-1.5">
                <Search className="text-muted-foreground shrink-0 size-3" aria-hidden="true" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find a column…"
                  aria-label="Find a column"
                  className="w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground/70 outline-none"
                />
              </div>
            )}
            <div className="max-h-72 overflow-y-auto py-1">
              {groups.length === 0 && (
                <p className="px-3 py-4 text-center text-2xs text-muted-foreground">
                  No column matches &ldquo;{query}&rdquo;
                </p>
              )}
              {groups.map((g) => (
                <div key={g.name || '__ungrouped'}>
                  {g.name && (
                    <p className="px-3 pt-2 pb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      {g.name}
                    </p>
                  )}
                  {g.items.map((opt) => {
                const visible = visibility[opt.id] !== false;
                // Keep at least ONE hideable column on screen — unchecking
                // the last visible one would blank the table (and strand
                // the operator, since the 3-dot menu vanishes with it).
                // The last-standing checkbox locks like a required column.
                const isLastVisible = visible
                  && !opt.alwaysVisible
                  && hideableVisibleCount <= 1;
                const disabled = opt.alwaysVisible === true || isLastVisible;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => !disabled && onToggle(opt.id)}
                    disabled={disabled}
                    className={cn(
                      'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left min-h-tap',
                      disabled
                        ? 'opacity-60 cursor-not-allowed'
                        : 'hover:bg-accent',
                    )}
                    title={
                      opt.alwaysVisible
                        ? 'Required column'
                        : isLastVisible
                          ? 'At least one column must stay visible'
                          : undefined
                    }
                  >
                    <span
                      className={cn(
                        'shrink-0 w-3.5 h-3.5 rounded border flex items-center justify-center',
                        visible
                          ? 'bg-primary border-primary'
                          : 'border-border',
                      )}
                    >
                      {visible && <Check className="text-primary-foreground size-3" aria-hidden="true" />}
                    </span>
                    <span className="flex-1 truncate text-foreground">{opt.label}</span>
                  </button>
                );
                  })}
                </div>
              ))}
            </div>
            <div className="border-t border-border">
              <button
                type="button"
                onClick={onReset}
                className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1.5 min-h-tap"
              >
                <RotateCcw className="size-3" aria-hidden="true" />
                Reset to defaults
              </button>
            </div>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
