# components/ui — shared primitive rules

Scoped rules for the design-system primitives in this folder. The main
dashboard rules live in [interfaces/dashboard/CLAUDE.md](../../../CLAUDE.md);
this file holds the per-primitive detail so the main file stays lean.

## Context menu / action menus (`context-menu.tsx`)

**The SSOT for every menu-of-actions in the dashboard** — right-click
menus AND click-dropdowns. Never hand-roll a `MenuPrimitive.Root`+items
block per page; that's the drift this replaced (Invites copy-menu,
TeamManagement member-picker were migrated off hand-rolled menus).

### Architecture: one data shape, one renderer, two openers

```
MenuAction[]          ← every consumer declares actions as plain data
      │
MenuActionList        ← renders + styles them ONCE (the popup body)
   ╱      ╲
ContextMenu    ActionMenu
(right-click   (click a visible
 at cursor)     ⋮ / button)
```

- `MenuAction`: `{ key, label, icon?, onSelect, disabled?, danger?,
  separatorBefore? }`. Icons are passed fully styled (e.g.
  `<Pencil size={14} className="text-muted-foreground" />`) so the caller
  owns colour/state; `danger: true` renders in the danger colour;
  `separatorBefore` draws a hairline above the item (group Delete off).
- Both openers render the same `MenuActionList`, so look, keyboard
  behavior, and theming can never diverge. Styling lives ONLY in this
  file's `ITEM` / `POPUP` constants (popover tokens, z-50 floating layer).

### The three usage patterns

| Attaching a menu to… | Use | Example |
|---|---|---|
| a DataGrid's rows | `rowActions={(row) => MenuAction[]}` prop on DataGrid | WorkOrders, Loads, Vehicles |
| any element (card / list item / row) | `<ContextMenu items render?>` wrapper | Applications links, Geofences zones, POI layers, Knowledge cards |
| a visible ⋮ / button (click, not right-click) | `<ActionMenu items><button/></ActionMenu>` | Invites copy-link, TeamManagement member picker |

### Contracts & gotchas

- **`render` prop**: `ContextMenu` wraps children in an inline `<span>` by
  default. Where a span can't legally wrap the target — a table row, a
  list item — pass `render={<tr …/>}` / `render={<li …/>}` and the
  trigger MERGES onto that element (it must forward refs: intrinsic
  element or forwardRef component; `TableRow` was converted for this).
- **Empty items**: `ContextMenu` with `items=[]` renders children
  untouched (a `render` element is kept via cloneElement so layout/keys
  survive) and does NOT hijack right-click — the browser menu comes
  through. `ActionMenu` with `items=[]` renders NOTHING, trigger
  included — if the trigger must stay visible (e.g. a disabled button),
  the CALLER renders it standalone when the list is empty (see
  TeamManagement's member picker).
- **Permission gating** happens in the caller's builder: return fewer
  items (or `[]`) when read-only. The menu must never offer an action the
  server would reject — reuse the SAME predicate the inline
  buttons/row-click use (e.g. Loads' `canEditLoad`).
- **Right-click is ADDITIVE** on rows/cards: keep left-click and inline
  buttons. Touch devices have no right-click, so nothing may be reachable
  ONLY through a context menu. (Exception by owner decision: personal
  saved-tab management in DataGrid is right-click only — a known keyboard/
  touch gap tracked for a focus-revealed fallback.)

### Where a feature's action list lives

**`features/<x>/contextMenu.tsx`** — a feature-local builder taking the
domain row + a `deps` object (handlers/permissions the component owns)
and returning `MenuAction[]`:

```tsx
export function loadRowMenu(
  load: LoadRow,                          // domain row
  deps: { canEditLoad: …; openEdit: … },  // component closures, passed IN
): MenuAction[]
```

The builder never reaches into component state — pages hand it their
closures, keeping it a pure, testable data function. Named `contextMenu`,
NOT `actions` — "actions" already means other things in this codebase
(DataGrid `bulkActions`, the column ⋮ menus, AI write actions). Tiny
one-off menus on non-grid surfaces may stay inline until they grow; a
`contextmenu/` FOLDER only when a feature accumulates several menu files.
