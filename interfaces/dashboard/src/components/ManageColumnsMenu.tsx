import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Check, RotateCcw, Columns3 } from 'lucide-react';
import { cn } from '../lib/utils';

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

export default function ManageColumnsMenu({
  options, visibility, onToggle, onReset, open, onOpenChange, anchor,
}: ManageColumnsMenuProps) {
  const hideableVisibleCount = options.filter(
    (o) => !o.alwaysVisible && visibility[o.id] !== false,
  ).length;
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
              <Columns3 size={14} className="text-muted-foreground" />
              <span className="text-xs font-medium text-foreground">Columns</span>
            </div>
            <div className="max-h-72 overflow-y-auto py-1">
              {options.map((opt) => {
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
                      'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left',
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
                      {visible && <Check size={12} className="text-primary-foreground" aria-hidden="true" />}
                    </span>
                    <span className="flex-1 truncate text-foreground">{opt.label}</span>
                  </button>
                );
              })}
            </div>
            <div className="border-t border-border">
              <button
                type="button"
                onClick={onReset}
                className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1.5"
              >
                <RotateCcw size={12} aria-hidden="true" />
                Reset to defaults
              </button>
            </div>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
