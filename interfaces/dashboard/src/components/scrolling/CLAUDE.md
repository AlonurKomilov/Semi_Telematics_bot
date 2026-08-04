# components/scrolling — the SSOT for scrolling surfaces

Import from the barrel (`components/scrolling`), never from the files
inside.

```
region.tsx      useScrollRegion() + <ScrollRegion>   ← the contract
scrollbars.tsx  ScrollbarH/V, useOverflow, useWheelToHorizontal
fit.ts          useFittedHeight()                    ← own your viewport
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

## `useFittedHeight` — a surface finds its own viewport

A table whose body scrolls inside its card, instead of growing to 250
rows and making the whole PAGE scroll, needs a definite height. Flexbox
can give it one — but only if every ancestor up to the scroll region
cooperates (`h-full flex flex-col min-h-0`), and **CSS cannot be
inverted**: a child has no way to impose that on its parents.

So the contract lived on the PAGE, as a `fillHeight` prop plus a class
recipe. Coverage after months: **3 of 40 grid surfaces.** That is the
whole argument. A convention every future page author must remember is
not a single source of truth; it is 40 opportunities to forget, and
every later change to scrolling behaviour is another N edits.

`useFittedHeight(el)` measures instead: it walks up to the nearest
scrolling ancestor and returns the room left below `el`, or `null`.

```tsx
const [card, setCard] = useState<HTMLDivElement | null>(null);
const fitted = useFittedHeight(card);
<div ref={setCard} style={fitted !== null ? { maxHeight: fitted } : undefined} />
```

Four things make it safe to switch on everywhere:

- **It fails OPEN.** No scroll ancestor, no layout (jsdom), no
  `ResizeObserver`, too little room — every one returns `null`, meaning
  "grow naturally, let the page scroll", which is exactly the behaviour
  that came before. A measurement that goes wrong loses an improvement;
  it cannot produce a broken layout.
- **`max-height`, never `height`.** A three-row table stays three rows
  tall instead of becoming a tall box with empty space under it.
- **The floor is the feature.** Below ~256px it declines. Pages that
  stack charts and KPI cards above their table fall through it on their
  own — those pages ARE taller than a screen, and page scrolling is
  correct for them. Nobody has to classify pages by hand. ±16px of
  hysteresis stops a layout sitting on the boundary from flapping.
- **The gap under the card is READ, not assumed** — the region's own
  `padding-bottom`, which is the shell's `p-4 lg:p-6`. A constant here
  silently disagrees the day the shell is restyled.

⚠️ **Three observers, and the third is the one you would omit.** Region,
the element's parent, and **the region's direct child (the page root)**.
Content arriving above the grid after first paint — a hero strip
resolving from its own query — moves the grid DOWN without resizing
either of the first two, so neither fires. Do not add more: the complete
set is three, and observing every ancestor "to be safe" is how you get a
real feedback loop.

⚠️ **The write must be idempotent.** Clamping the card shortens the page,
which fires the page-root observer, which measures again. It gets the
same answer — neither the region's height nor the element's top depends
on the element's height — and the `>1px` epsilon skips the write, so the
cascade stops on the second tick. Remove that guard and the two feed each
other forever.

Pass `enabled: false` where an internally-scrolling surface would be
wrong: a table inside a chat message, where the conversation is what the
reader scrolls.

## The custom painted bars are narrow by design

`ScrollbarH` / `ScrollbarV` exist for ONE reason: with pinned columns a
native bar spans the whole container, implying the frozen columns scroll
too. **No pinned columns → use a plain scroll region and the browser's
own bar**, which `index.css` already themes.

### The bar must never reach the header — and a lane is NOT the fix

`ScrollbarV` is offset below the sticky header (`insetTop`) for the
reason it is custom at all: a native bar spans the container's full
height, up alongside the column labels and their ⋮ menus, which reads as
the rows scrolling *into* the header. That offset IS the measured header
height, so **the bar must not render until the measurement lands** — a
full-height bar against the header for one frame is exactly the thing
being prevented. Guard on `headerHeight > 0`, not just on `bodyScrolls`.

⚠️ **Do not "reserve a lane" for it.** A 12px gutter (wrapper padding)
was tried and reverted the same day. It is wrong twice over:

- The lane runs the full height, so it exists beside the HEADER too —
  putting the scrollbar's territory in the one place the bar is
  forbidden from. Matching its background to the band only hides that
  in one theme's worth of luck.
- It moves the last column away from the grid's edge. A table's final
  column ending 12px short reads as a rendering fault, and the reports
  say so: *"the last column is not staying at the last of the datagrid."*

The bar is an overlay, invisible until hover, 8px at the extreme edge —
the macOS/iOS convention. If content under it is genuinely unreadable,
the answer is the column's width or the page's column config, not a
gutter carved out of the table.

**No fade at the clipped edge either.** A right-edge gradient was tried
on the record grid and removed: it veils real data whenever the table
overflows, *including* when you are already scrolled to the end and
there is nothing more to promise. The horizontal bar below the rows
already says "this scrolls sideways". The fade stays in `DrillDialog`,
which shows no bar at all.

### Hide the native bar; do NOT switch the axis off

Both grids ran `overflow-x: hidden` on the reasoning that `overflow-x:
auto` reserves a scrollbar track at the container's bottom *"even with
`::-webkit-scrollbar { height: 0 }`"*. That is true of `height: 0` — and
it is **not** what `HIDE_NATIVE_SCROLLBAR` does. It sets
`scrollbar-width: none` + `::-webkit-scrollbar { display: none }`, which
removes the bar rather than shrinking it, and reserves nothing.

The cost of getting that wrong was large and invisible: `hidden` leaves
the browser with **no horizontal scrolling mechanism at all** — no touch
pan, no keyboard, no autoscroll — so a 61-column matrix was reachable
only by dragging an 8px painted thumb (**WCAG 2.1.1**). Meanwhile the
VERTICAL axis had been running `auto` + `HIDE_NATIVE_SCROLLBAR` the whole
time, with its own comment saying *"scrolling itself is untouched; only
the painting moves."* One axis was right for months while the other was
switched off on stale grounds.

**So: both axes `auto`, native bars hidden, painted bars on top.**

⚠️ `useWheelToHorizontal` exists only for the `overflow-x: hidden` shape,
and therefore has **zero consumers** — it is kept until the switch is
confirmed in a browser, then deleted (the bar for an export here is two
real consumers). Do not reach for it: with `auto` the browser applies
`deltaX` itself, so calling it as well moves the container **twice per
swipe**. That is not hypothetical — it is the exact bug that shipped when
the bridge lived inside `useScrollMetrics` and both scrollbars installed
it.

## Enforcement

A folder people *can* use is a suggestion. This codebase makes a thing an
SSOT by banning the alternative, the way `title=` is banned in favour of
`<Tip>`.

**What shipped:** `eslint.config.js` bans the **hand-rolled modal
backdrop** — `className` matching `fixed inset-0 … bg-black/`. That
pattern is never correct: 12 existed, every one missing the same four
things (focus trap, Escape, `aria-modal`, background scroll lock). Use
`<Sheet>` for a side drawer, `<Dialog>` for a centred one.

The rule earned its place immediately: a `bg-black/60` grep found the 12
I converted; the rule found **16 more** at other opacities. A grep finds
what you thought to look for.

**What was REJECTED, and why it matters more than what shipped:** a ban
on raw `overflow-*` classes, routing every scroller through this module.
It would flag ~50 sites of which **~45 are correct** — menus, pickers,
dialog bodies — because the refusal rule above says a plain overflow div
is right for those. A rule that cries wolf 45 times to catch 5 gets
disabled wholesale.

⚠️ The `title=` analogy does not carry, and that is the general lesson:
**native `title=` is never correct; a plain scroller often is.** Ban the
pattern that is always wrong, never the primitive that is usually right.

The two primitives (`ui/dialog.tsx`, `ui/sheet.tsx`) are exempted inline
with the reason — their own overlays cannot be written in terms of
themselves. Legitimate exceptions carry an `eslint-disable` **with a
reason**, the same convention the z-index ladder uses for Leaflet panes.
Put the directive on the line ADJACENT to the code: on the first line of
a multi-line comment it disables the comment, not the code.
