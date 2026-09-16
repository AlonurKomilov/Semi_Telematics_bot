import { cloneElement, Fragment } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { Menu } from '@base-ui/react/menu';
import { ContextMenu as Base } from '@base-ui/react/context-menu';
import { Popover } from '@base-ui/react/popover';
import { Tip } from '@/components/tooltip';
import { cn } from '@/lib/utils';

/**
 * Context menu — a right-click menu, AND the shared action-list renderer
 * behind menus across the app.
 *
 * THE ARCHITECTURE (why this is reusable everywhere, not just for tabs):
 * a right-click context menu and a ⋮ dropdown are the SAME list of
 * actions — they differ only in HOW they open (right-click at the cursor
 * vs. a trigger button).  Base UI models exactly that: ``ContextMenu``
 * re-exports Menu's ``Item`` / ``Popup`` / ``Separator`` parts and its
 * Root provides the same ``MenuRootContext``, so ONE item renderer styles
 * both.  Any surface declares its actions once as ``MenuAction[]`` data
 * and exposes them via right-click (``<ContextMenu>``) — a future ⋮
 * dropdown opener would render the SAME ``<MenuActionList>`` inside a
 * ``Menu.Root`` with a trigger button.
 *
 * Consumers: DataGrid tabs (first), and any list row / card / grid row
 * with more than one or two actions.  Declare actions as data; wrap the
 * right-clickable element in ``<ContextMenu items={…}>``.
 */

export interface MenuAction {
  /** Stable key for React (also handy for tests). */
  key: string;
  label: ReactNode;
  /** Fully-styled icon node — pass e.g. ``<Pencil className="size-3.5" … />``
   *  so the caller owns colour/state (the star-toggle case needs this).
   *  A CLASS, not the `size` prop: that prop writes an SVG width/height
   *  attribute, which no Size axis can reach. This line used to show the
   *  prop form, which is how a doc comment ships the bug it documents. */
  icon?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  /** Destructive action — renders in the danger colour. */
  danger?: boolean;
  /** Draw a hairline divider ABOVE this item (groups Delete off, etc.). */
  separatorBefore?: boolean;
}

const ITEM =
  // min-h-tap: the row measures 28px today, comfortably over the WCAG
  // 2.5.8 floor — but its height is `py-1.5` plus a line box, and BOTH
  // ride the Size axes, so it falls through the floor as soon as the
  // user shrinks. The clamp costs nothing at the default multiplier.
  'w-full flex items-center gap-2 px-3 py-1.5 min-h-tap text-xs cursor-pointer ' +
  'outline-none data-[highlighted]:bg-accent ' +
  'data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed';

const POPUP =
  'min-w-48 max-h-[min(20rem,70vh)] overflow-y-auto surface surface-popover ' +
  'text-popover-foreground border border-border rounded-md shadow-lg ' +
  'py-1 outline-none';

/**
 * The menu body — renders ``MenuAction[]`` as styled items + separators.
 * Works inside EITHER a ``ContextMenu.Root`` (right-click) or a
 * ``Menu.Root`` (dropdown), because ContextMenu reuses ``Menu.Item`` and
 * provides the same root context.  Style menus here, once.
 */
export function MenuActionList({ items }: { items: MenuAction[] }) {
  return (
    <>
      {items.map((a) => (
        <Fragment key={a.key}>
          {a.separatorBefore && <div className="my-1 border-t border-border" />}
          <Menu.Item
            disabled={a.disabled}
            onClick={a.onSelect}
            className={cn(ITEM, a.danger ? 'text-danger' : 'text-foreground')}
          >
            {a.icon}
            {a.label}
          </Menu.Item>
        </Fragment>
      ))}
    </>
  );
}

export interface ContextMenuProps {
  /** Actions shown on right-click.  Empty → no menu (children pass through). */
  items: MenuAction[];
  /** The right-clickable content.  By default wrapped in an inline trigger
   *  span; with ``render`` it becomes the render element's content. */
  children: ReactNode;
  /** Extra classes for the default inline trigger span (ignored when
   *  ``render`` is set). */
  className?: string;
  /** Turn the menu off without unwrapping the tree (children still render). */
  disabled?: boolean;
  /** Merge the right-click trigger onto THIS element instead of wrapping
   *  ``children`` in a span — required for elements that can't legally sit
   *  inside a span, e.g. a table row (``<tr>`` via ``TableRow``).  The
   *  element receives ``children`` as its content and MUST forward
   *  refs + props (an intrinsic element or a forwardRef component). */
  render?: ReactElement;
}

/**
 * Wrap any element so a RIGHT-CLICK on it opens ``items`` at the cursor.
 * The menu is portalled and z-50 (design.md floating layer); the cursor
 * anchoring is handled by Base UI's ContextMenu positioner.
 *
 * Default: wraps ``children`` in an inline span (good for chips, cards,
 * list items).  For table rows pass ``render={<TableRow … />}`` so the
 * trigger merges onto the ``<tr>`` — a span can't wrap a row.
 */
export function ContextMenu({ items, children, className, disabled, render }: ContextMenuProps) {
  if (disabled || items.length === 0) {
    // No menu → drop only the trigger wiring.  A ``render`` element (a
    // <tr>/<div> whose layout + list key the parent depends on) is kept
    // with its children; the default span wrapper is pure decoration, so
    // children pass straight through.
    return render ? cloneElement(render, undefined, children) : <>{children}</>;
  }
  const trigger = render ?? (<span className={cn('inline-flex', className)} /> as ReactElement);
  return (
    <Base.Root>
      <Base.Trigger render={trigger}>{children}</Base.Trigger>
      <Base.Portal>
        <Base.Positioner className="z-50 outline-none" sideOffset={4}>
          <Base.Popup className={POPUP}>
            <MenuActionList items={items} />
          </Base.Popup>
        </Base.Positioner>
      </Base.Portal>
    </Base.Root>
  );
}

export interface AnchoredMenuProps {
  /** Actions shown while open.  Empty → nothing renders. */
  items: MenuAction[];
  /** The element the popup positions against — usually the cell/row the
   *  user clicked.  null → nothing renders. */
  anchor: HTMLElement | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Popup alignment relative to the anchor (default "start"). */
  align?: 'start' | 'center' | 'end';
}

/**
 * The third opener: ONE controlled menu instance positioned against any
 * element the caller names.  For grids where hundreds of cells share
 * one action vocabulary — mounting a ``Menu.Root`` per cell makes a
 * large grid expensive to render, while one shared instance costs the
 * same as a single ⋮ menu.  The caller owns the open state and the
 * anchor (typically ``onClick={(e) => open(e.currentTarget)}`` on each
 * cell, which should carry ``aria-haspopup="menu"``); closing returns
 * focus to the anchor.  Same ``MenuAction[]`` data, same
 * ``MenuActionList`` renderer as the other two openers.
 */
export function AnchoredMenu({ items, anchor, open, onOpenChange, align = 'start' }: AnchoredMenuProps) {
  if (anchor == null || items.length === 0) return null;
  return (
    <Menu.Root open={open} onOpenChange={onOpenChange}>
      <Menu.Portal>
        <Menu.Positioner anchor={anchor} className="z-50 outline-none" align={align} sideOffset={4}>
          <Menu.Popup className={POPUP} finalFocus={{ current: anchor }}>
            <MenuActionList items={items} />
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/**
 * A panel that hangs off a trigger, whose BODY is not a list of actions.
 *
 * `ActionMenu` is the right answer whenever a menu is `MenuAction[]`
 * data. Two panels in the topbar are not: the account menu opens with a
 * name and a role above its items, and the persona selector is a
 * LISTBOX with a tier flyout on a row. Popover rather than Menu for
 * exactly that reason — a `Menu.Popup` is `role="menu"`, and a listbox
 * inside a menu is two contradictory answers to "what is this". A
 * popover asserts nothing, so each caller keeps the semantics its
 * content already has.
 *
 * WHY THEY HAD TO LEAVE THE TOPBAR. An element carrying a
 * `backdrop-filter` becomes a BACKDROP ROOT: a descendant's own
 * backdrop-filter then sees only what is painted inside that root,
 * which is nothing. So while these two sat inside the header, the
 * header could never be glass — the day it was, both would stop
 * occluding and show the page straight through themselves. That is not
 * hypothetical; it is the bug that was reported and fixed once.
 * Portalled, they are nobody's descendants and the frame is free.
 *
 * It ends a second bug class on its own. An inline panel is at the
 * mercy of every ancestor: the persona flyout was erased — not clipped,
 * ABSENT — by an `overflow-hidden` two levels up, and the language menu
 * once ran off the left of a phone because it measured nothing. The
 * positioner here shifts to stay on screen.
 *
 * `tip` rather than a `<Tip>` around the caller's button: BOTH are
 * base-ui triggers, and they compose only one way round — the tooltip
 * renders the popover trigger, which renders the button. Wrapped the
 * other way the popover clones `Tip`, which takes four named props and
 * spreads none, so every prop that makes a trigger a trigger is
 * dropped in silence.
 *
 * Controlled on purpose: both callers already own their open state and
 * close it themselves after navigating, so the primitive is asked for
 * placement and nothing else.
 */
export function Dropdown({
  open, onOpenChange, trigger, tip, align = 'end', className, children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The trigger element. Must forward refs and props. */
  trigger: ReactElement;
  /** Hover label for the trigger, if it needs one. */
  tip?: string;
  align?: 'start' | 'center' | 'end';
  /** Extra classes on the popup — a width, usually. */
  className?: string;
  children: ReactNode;
}) {
  const anchor = <Popover.Trigger render={trigger} />;
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      {tip ? <Tip label={tip}>{anchor}</Tip> : anchor}
      <Popover.Portal>
        <Popover.Positioner className="z-50 outline-none" align={align} sideOffset={4}>
          {/* The chrome a panel needs and nothing about its insides —
              no padding and no min-width, because both callers bring
              their own sections. `.surface` and `.surface-popover` are
              what make it the same material as every other floating
              thing under glass. */}
          <Popover.Popup
            className={cn(
              'surface surface-popover text-popover-foreground border border-border',
              'rounded-md shadow-lg outline-none',
              className,
            )}
          >
            {children}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

export interface ActionMenuProps {
  /** Actions shown when the trigger is clicked.  Empty → nothing renders. */
  items: MenuAction[];
  /** The trigger element (e.g. a ⋮ icon button).  Must forward refs +
   *  props — an intrinsic element or a forwardRef component. */
  children: ReactElement;
  /** Popup alignment relative to the trigger (default "end"). */
  align?: 'start' | 'center' | 'end';
}

/**
 * The CLICK-triggered sibling of ``ContextMenu`` — a ⋮ / button dropdown
 * that renders the SAME ``MenuActionList`` from the SAME ``MenuAction[]``.
 * Use it where a visible affordance is wanted (a per-row ⋮ button) instead
 * of right-click; use ``ContextMenu`` for right-click at the cursor.  One
 * action list, two openers.
 *
 * CONTRACT: empty ``items`` renders NOTHING — the trigger disappears too.
 * If the trigger must stay visible when there are no actions (e.g. a
 * disabled button), the CALLER renders that trigger itself when the list
 * is empty (see TeamManagement's member picker).
 */
export function ActionMenu({ items, children, align = 'end' }: ActionMenuProps) {
  if (items.length === 0) return null;
  return (
    <Menu.Root>
      <Menu.Trigger render={children} />
      <Menu.Portal>
        <Menu.Positioner className="z-50 outline-none" align={align} sideOffset={4}>
          <Menu.Popup className={POPUP}>
            <MenuActionList items={items} />
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
