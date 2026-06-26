import { Menu as MenuPrimitive } from '@base-ui/react/menu';
import {
  ArrowUp, ArrowDown, X, EyeOff, Columns3, MoreVertical,
  ArrowLeftToLine, ArrowRightToLine, PinOff,
} from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * 3-dot column actions menu.  Sits at the right edge of every
 * column header when the host table is opted-in (DataTable receives
 * a ``tableId`` prop).
 *
 *   ┌─────────────────────────────┐
 *   │ ↑  Sort ascending           │
 *   │ ↓  Sort descending          │
 *   │ ×  Clear sort               │  (only when currently sorted)
 *   ├─────────────────────────────┤
 *   │ ◇  Hide column              │
 *   │ ⚙  Manage columns…          │
 *   └─────────────────────────────┘
 *
 * The trigger is a small ``⋮`` button that becomes more prominent on
 * hover.  Filter lives in its own popover on the label (unchanged) —
 * the 3-dot menu intentionally doesn't duplicate it, since filter
 * needs a persistent multi-select surface that doesn't fit the menu
 * UX (items dismiss on click).
 */

interface ColumnHeaderMenuProps {
  sorted: false | 'asc' | 'desc';
  canSort: boolean;
  onSortAsc: () => void;
  onSortDesc: () => void;
  onClearSort: () => void;
  onHide: () => void;
  onManage: () => void;
  /** Pin state — ``false`` = unpinned, otherwise the side it's
   *  pinned to.  Drives which pin/unpin items render. */
  pinned: false | 'left' | 'right';
  onPinLeft: () => void;
  onPinRight: () => void;
  onUnpin: () => void;
  /** Aria label for the trigger — operators using screen readers. */
  columnLabel: string;
}

export default function ColumnHeaderMenu({
  sorted, canSort, onSortAsc, onSortDesc, onClearSort, onHide, onManage,
  pinned, onPinLeft, onPinRight, onUnpin,
  columnLabel,
}: ColumnHeaderMenuProps) {
  return (
    <MenuPrimitive.Root>
      <MenuPrimitive.Trigger
        render={(props) => (
          <button
            type="button"
            {...props}
            aria-label={`${columnLabel} column options`}
            // stopPropagation on the wrapping header onClick handler
            // (legacy sort-on-row-click).  The menu is its own
            // action surface; don't double-fire sort underneath it.
            onClick={(e) => {
              e.stopPropagation();
              props.onClick?.(e);
            }}
            className="p-0.5 text-muted-foreground hover:text-foreground opacity-50 hover:opacity-100"
          >
            <MoreVertical size={14} />
          </button>
        )}
      />
      <MenuPrimitive.Portal>
        <MenuPrimitive.Positioner align="end" sideOffset={4} className="z-50 outline-none">
          <MenuPrimitive.Popup className="min-w-44 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
            {canSort && (
              <>
                <MenuItem
                  icon={<ArrowUp size={14} />}
                  label="Sort ascending"
                  active={sorted === 'asc'}
                  onClick={onSortAsc}
                />
                <MenuItem
                  icon={<ArrowDown size={14} />}
                  label="Sort descending"
                  active={sorted === 'desc'}
                  onClick={onSortDesc}
                />
                {sorted && (
                  <MenuItem
                    icon={<X size={14} />}
                    label="Clear sort"
                    onClick={onClearSort}
                  />
                )}
                <div className="my-1 border-t border-border" />
              </>
            )}
            <MenuItem
              icon={<ArrowLeftToLine size={14} />}
              label="Pin left"
              active={pinned === 'left'}
              onClick={onPinLeft}
            />
            <MenuItem
              icon={<ArrowRightToLine size={14} />}
              label="Pin right"
              active={pinned === 'right'}
              onClick={onPinRight}
            />
            {pinned !== false && (
              <MenuItem
                icon={<PinOff size={14} />}
                label="Unpin"
                onClick={onUnpin}
              />
            )}
            <div className="my-1 border-t border-border" />
            <MenuItem
              icon={<EyeOff size={14} />}
              label="Hide column"
              onClick={onHide}
            />
            <MenuItem
              icon={<Columns3 size={14} />}
              label="Manage columns…"
              onClick={onManage}
            />
          </MenuPrimitive.Popup>
        </MenuPrimitive.Positioner>
      </MenuPrimitive.Portal>
    </MenuPrimitive.Root>
  );
}

function MenuItem({
  icon, label, active, onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <MenuPrimitive.Item
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none',
        'data-[highlighted]:bg-accent',
        active && 'text-primary',
      )}
      onClick={onClick}
    >
      <span className="shrink-0 w-4 flex justify-center text-muted-foreground">
        {icon}
      </span>
      <span className="flex-1 text-foreground">{label}</span>
    </MenuPrimitive.Item>
  );
}
