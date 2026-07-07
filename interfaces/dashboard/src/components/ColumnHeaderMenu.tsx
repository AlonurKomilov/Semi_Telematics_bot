import { Menu as MenuPrimitive } from '@base-ui/react/menu';
import {
  ArrowUp, ArrowDown, X, EyeOff, Columns3, MoreVertical,
  Pin, PinOff, Filter as FilterIcon, ArrowUpDown, ChevronRight,
  ArrowLeftToLine, ArrowRightToLine,
  Group as GroupIcon, Ungroup as UngroupIcon, Plus, Check, ListTree,
  MoveHorizontal,
} from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * 3-dot column actions menu.  Sits at the right edge of every
 * column header when the host table is opted-in (DataGrid receives
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
   *  pinned to.  When unpinned the menu shows a "Pin →" submenu
   *  with explicit Left / Right choices (parallel to the Sort
   *  submenu).  When pinned the menu shows a single "Unpin" item. */
  pinned: false | 'left' | 'right';
  onPinLeft: () => void;
  onPinRight: () => void;
  onUnpin: () => void;
  /** Filter — when ``canFilter`` is true the menu renders a "Filter…"
   *  item that opens the same multi-select popover the inline filter
   *  icon does.  Lets keyboard / 3-dot-menu users reach the filter
   *  without hunting for the small ▽ icon on the column label.
   *  ``filterActive`` shows a count badge in the menu when the column
   *  is currently narrowing the rows. */
  canFilter: boolean;
  onFilter: () => void;
  filterActive: number;
  /** Column grouping — "Group →" submenu listing the existing group
   *  names (assign this column to one), a "New group…" creator, and
   *  "Remove from group" when the column currently belongs to one.
   *  ``currentGroup`` is the column's effective group (config default
   *  overlaid by the operator's per-user overrides). */
  groupNames: string[];
  currentGroup: string | null;
  onAssignGroup: (name: string) => void;
  onNewGroup: () => void;
  onUngroup: () => void;
  /** ROW grouping (distinct from the column-bracket grouping above):
   *  collapse the data rows under this column's values.  ``rowGrouped``
   *  = this column is the active row-group key. */
  rowGrouped: boolean;
  onRowGroup: () => void;
  /** Fit the column's width to its widest rendered cell. */
  onAutosize: () => void;
  /** Aria label for the trigger — operators using screen readers. */
  columnLabel: string;
}

export default function ColumnHeaderMenu({
  sorted, canSort, onSortAsc, onSortDesc, onClearSort, onHide, onManage,
  pinned, onPinLeft, onPinRight, onUnpin,
  canFilter, onFilter, filterActive,
  groupNames, currentGroup, onAssignGroup, onNewGroup, onUngroup,
  rowGrouped, onRowGroup, onAutosize,
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
                <MenuPrimitive.SubmenuRoot>
                  <MenuPrimitive.SubmenuTrigger
                    className={cn(
                      'w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none',
                      'data-[highlighted]:bg-accent data-[popup-open]:bg-accent',
                      sorted && 'text-primary',
                    )}
                  >
                    <span className="shrink-0 w-4 flex justify-center text-muted-foreground">
                      <ArrowUpDown size={14} />
                    </span>
                    <span className="flex-1 text-foreground text-left">Sort</span>
                    {sorted && (
                      <span aria-hidden="true" className="text-muted-foreground">
                        {sorted === 'asc'
                          ? <ArrowUp size={12} />
                          : <ArrowDown size={12} />}
                      </span>
                    )}
                    <ChevronRight size={12} className="text-muted-foreground" aria-hidden="true" />
                  </MenuPrimitive.SubmenuTrigger>
                  <MenuPrimitive.Portal>
                    <MenuPrimitive.Positioner
                      side="inline-end"
                      align="start"
                      sideOffset={4}
                      className="z-50 outline-none"
                    >
                      <MenuPrimitive.Popup className="min-w-40 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
                        <MenuItem
                          icon={<ArrowUp size={14} />}
                          label="Ascending"
                          active={sorted === 'asc'}
                          onClick={onSortAsc}
                        />
                        <MenuItem
                          icon={<ArrowDown size={14} />}
                          label="Descending"
                          active={sorted === 'desc'}
                          onClick={onSortDesc}
                        />
                        {sorted && (
                          <>
                            <div className="my-1 border-t border-border" />
                            <MenuItem
                              icon={<X size={14} />}
                              label="Clear sort"
                              onClick={onClearSort}
                            />
                          </>
                        )}
                      </MenuPrimitive.Popup>
                    </MenuPrimitive.Positioner>
                  </MenuPrimitive.Portal>
                </MenuPrimitive.SubmenuRoot>
                <div className="my-1 border-t border-border" />
              </>
            )}
            {canFilter && (
              <>
                <MenuItem
                  icon={<FilterIcon size={14} />}
                  label="Filter…"
                  active={filterActive > 0}
                  badge={filterActive > 0 ? String(filterActive) : undefined}
                  onClick={onFilter}
                />
                <div className="my-1 border-t border-border" />
              </>
            )}
            {pinned === false ? (
              <MenuPrimitive.SubmenuRoot>
                <MenuPrimitive.SubmenuTrigger
                  className={cn(
                    'w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none',
                    'data-[highlighted]:bg-accent data-[popup-open]:bg-accent',
                  )}
                >
                  <span className="shrink-0 w-4 flex justify-center text-muted-foreground">
                    <Pin size={14} />
                  </span>
                  <span className="flex-1 text-foreground text-left">Pin</span>
                  <ChevronRight size={12} className="text-muted-foreground" aria-hidden="true" />
                </MenuPrimitive.SubmenuTrigger>
                <MenuPrimitive.Portal>
                  <MenuPrimitive.Positioner
                    side="inline-end"
                    align="start"
                    sideOffset={4}
                    className="z-50 outline-none"
                  >
                    <MenuPrimitive.Popup className="min-w-40 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
                      <MenuItem
                        icon={<ArrowLeftToLine size={14} />}
                        label="Pin to Left"
                        onClick={onPinLeft}
                      />
                      <MenuItem
                        icon={<ArrowRightToLine size={14} />}
                        label="Pin to Right"
                        onClick={onPinRight}
                      />
                    </MenuPrimitive.Popup>
                  </MenuPrimitive.Positioner>
                </MenuPrimitive.Portal>
              </MenuPrimitive.SubmenuRoot>
            ) : (
              <MenuItem
                icon={<PinOff size={14} />}
                label="Unpin"
                active
                onClick={onUnpin}
              />
            )}
            {/* Group → submenu: assign this column to an existing
                group, mint a new one, or pull it out of its current
                group.  Groups render as the bracket band above the
                column headers. */}
            <MenuPrimitive.SubmenuRoot>
              <MenuPrimitive.SubmenuTrigger
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none',
                  'data-[highlighted]:bg-accent data-[popup-open]:bg-accent',
                  currentGroup && 'text-primary',
                )}
              >
                <span className="shrink-0 w-4 flex justify-center text-muted-foreground">
                  <GroupIcon size={14} />
                </span>
                <span className="flex-1 text-foreground text-left">Group</span>
                {currentGroup && (
                  <span className="text-2xs text-muted-foreground max-w-20 truncate">
                    {currentGroup}
                  </span>
                )}
                <ChevronRight size={12} className="text-muted-foreground" aria-hidden="true" />
              </MenuPrimitive.SubmenuTrigger>
              <MenuPrimitive.Portal>
                <MenuPrimitive.Positioner
                  side="inline-end"
                  align="start"
                  sideOffset={4}
                  className="z-50 outline-none"
                >
                  <MenuPrimitive.Popup className="min-w-44 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
                    {groupNames.map((name) => (
                      <MenuItem
                        key={name}
                        icon={name === currentGroup
                          ? <Check size={14} />
                          : <GroupIcon size={14} />}
                        label={name}
                        active={name === currentGroup}
                        onClick={() => onAssignGroup(name)}
                      />
                    ))}
                    {groupNames.length > 0 && (
                      <div className="my-1 border-t border-border" />
                    )}
                    <MenuItem
                      icon={<Plus size={14} />}
                      label="New group…"
                      onClick={onNewGroup}
                    />
                    {currentGroup && (
                      <MenuItem
                        icon={<UngroupIcon size={14} />}
                        label="Remove from group"
                        onClick={onUngroup}
                      />
                    )}
                  </MenuPrimitive.Popup>
                </MenuPrimitive.Positioner>
              </MenuPrimitive.Portal>
            </MenuPrimitive.SubmenuRoot>
            {/* ROW grouping toggle — collapses the data rows under
                this column's values.  Named "rows" explicitly so
                operators don't confuse it with the column-bracket
                "Group" submenu above. */}
            <MenuItem
              icon={<ListTree size={14} />}
              label={rowGrouped ? 'Ungroup rows' : 'Group rows by this'}
              active={rowGrouped}
              onClick={onRowGroup}
            />
            <MenuItem
              icon={<MoveHorizontal size={14} />}
              label="Autosize column"
              onClick={onAutosize}
            />
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
  icon, label, active, onClick, badge,
}: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
  /** Small trailing count chip — used to show the active filter
   *  selection size next to "Filter…" so the menu mirrors what the
   *  inline filter trigger shows. */
  badge?: string;
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
      {badge && (
        <span className="text-2xs bg-primary/15 text-primary px-1 rounded">
          {badge}
        </span>
      )}
    </MenuPrimitive.Item>
  );
}
