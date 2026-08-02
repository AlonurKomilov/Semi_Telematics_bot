# components/scrolling — the SSOT for scrolling surfaces

Import from the barrel (`components/scrolling`), never from the files
inside.

```
region.tsx      useScrollRegion() + <ScrollRegion>   ← the contract
scrollbars.tsx  ScrollbarH/V, useOverflow, useWheelToHorizontal
```

## Why this exists

Before it, the app had **57 files with a scrolling surface and 2 that
were focusable** — both in the grid family. `overscroll-behavior`
appeared **zero times** in the whole codebase. Every panel, drawer and
list built since had re-decided the same four questions from scratch,
and mostly decided wrong.

## The contract — what `<ScrollRegion>` adds to a plain overflow div

A `overflow-y-auto` div is not a scroll region; it is a box that clips.
Four things are missing by default, and each is a real defect, not a
nicety:

| | Why |
|---|---|
| `tabIndex={0}` | A plain overflow div is NOT focusable, so a keyboard user cannot scroll it **at all** — everything past the first screen becomes mouse-only (**WCAG 2.1.1, Level A**) |
| `role="region"` + name | So it can be found and entered rather than being an anonymous box. **Only when a label is given** — an unnamed landmark is worse than none |
| `overscroll-contain` | Reaching the end otherwise chains the scroll to whatever is behind; inside a modal that reads as the app coming apart |
| `scroll-padding` | The browser's scroll-into-view puts a tabbed-to element at the scrollport's literal edge — i.e. **behind** a sticky header or frozen column. The focused thing is "in view" and invisible (**WCAG 2.4.11**) |

### The contract is inline STYLE, not classes

Deliberately, and it was a class-based design first. A caller passing
`className="overflow-hidden"` produced `overflow-hidden overflow-y-auto`
from tailwind-merge — different merge groups, so it keeps BOTH — and
which axis then wins is decided by **the order Tailwind emits its CSS**,
not by the class attribute. Unknowable from the call site, invisible in
jsdom, and the losing outcome is a focusable, named region a keyboard
user can enter and cannot scroll: a fresh WCAG 2.1.1 defect manufactured
by the component built to prevent it.

Inline style outranks every class, so the contract cannot be deleted by
accident. `className` belongs entirely to the caller for layout. To
change the overflow use `axis`; to allow chaining use
`allowScrollChaining`. Both typed, both greppable, neither reachable by
mistake.

### `axis` is two independent axes, because CSS is

`'y' | 'x' | 'both' | { y?, x? }`. The shorthands cover the common cases;
the OBJECT form exists because the three-value enum could name only three
of nine combinations — and missed `{ y: 'auto', x: 'hidden' }`, which is
what **every surface with painted scrollbars** needs and is this module's
own documented precondition for `useWheelToHorizontal`. A contract that
cannot state its own precondition is not a contract.

### `resetKey`

Change it and the pane returns to the top, because the content is now a
DIFFERENT list. Typed `unknown` and compared by the effect's own
dependency identity, so pass a primitive **or a reference you know is
memo-stable** — an object rebuilt each render resets on every parent
render, which is exactly the bug that made the pivot report jump to row 1
on unrelated state changes. `undefined` means never reset.

It **skips its first run**. A fresh element is already at 0, so a mount
reset can only ever destroy someone else's work: React flushes layout
effects before passive ones, so a parent restoring a saved offset in
`useLayoutEffect` would be written and then zeroed. It also resets the
horizontal axis when the region owns it — otherwise `axis="x"` would
accept a `resetKey` and silently do nothing.

## Two doors, one implementation

`useScrollRegion()` returns `{ ref, node, nodeRef, props }`;
`<ScrollRegion>` is thin over it and forwards ordinary div attributes
(`id`, `onScroll`, `data-*`) — a wrapper that swallowed them would send
callers straight back to a hand-rolled div.

The hook is the PRIMARY export, not a convenience. The two hardest
consumers — `DataGrid` and `PivotView` — own their own container and
compose their own classes onto it. A wrapper they could not use would
not be a single source of truth; it would be a second one.

`node` is published through a **callback ref**: the grid unmounts and
remounts its scroller when pivot toggles, and any effect keyed on
anything but the ELEMENT ends up observing a detached node — at which
point measurement freezes and the scrollbars silently stop rendering.

## ⚠️ What this module REFUSES

**Not everything that scrolls is a scroll region.**

> If the user **reads and navigates** the content → `ScrollRegion`.
> If it is a **short menu, dropdown or picker list** → a plain
> `overflow-y-auto` div is correct.

Wrapping a five-item menu in a landmark makes the page noisier for a
screen-reader user, not clearer. `dropdown-menu.tsx`,
`ColumnFilterMenu`, `VendorPicker` and `DateRangePresets` stay as they
are, deliberately.

Also **not** here, each having had fewer than two real consumers:

| Rejected | Why |
|---|---|
| `useDragAutoScroll` | dnd-kit already auto-scrolls with `PointerSensor`; native HTML5 DnD is auto-scrolled by the browser |
| `HScrollStrip` | A class constant belongs next to `HeroChip`, not a component |
| `useStickToBottom` | Two call sites in ONE file (`Chat.tsx`) — a local helper |
| `useScrollActiveIntoView` | One caller; and an index-keyed API cannot map index→node generally |
| `STICKY_BAND` | A z-tier belongs to `design.md`, which already owns the ladder |

The bar for adding an export: **two real consumers the existing
primitives have not already absorbed.**

## The custom painted bars are narrow by design

`ScrollbarH` / `ScrollbarV` exist for ONE reason: with pinned columns a
native bar spans the whole container, implying the frozen columns scroll
too. **No pinned columns → use a plain scroll region and the browser's
own bar**, which `index.css` already themes.

⚠️ `useWheelToHorizontal` has a **precondition**: the container must be
`overflow-x: hidden`. It exists to put back a gesture the browser is
ignoring. Called on a container that is already `overflow-x: auto` it
**doubles every trackpad swipe** — which is precisely the bug that
shipped when it lived inside `useScrollMetrics` and both bars installed
it. Call it **once**, from whoever owns the container.

## Enforcement

A folder people *can* use is a suggestion. This is an SSOT the same way
`components/tooltip` is: `eslint.config.js` bans the alternative (there,
native `title=`). The equivalent rule for raw `overflow-*` classes lands
once the migration is done — turning it on first would blow the
`--max-warnings` budget across 57 files.

Legitimate exceptions carry an `eslint-disable` **with a reason**, the
same convention the z-index ladder uses for Leaflet panes.
