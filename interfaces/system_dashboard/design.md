# Operator console — the rules the pages share

The customer dashboard has [design.md](../dashboard/design.md); this
console had nothing, and it showed: five pages had each declared their
own `btnCls`, four identical and one quietly wider, and the same
meanings were written twice — `text-danger` on one page and
`text-rose-300` on the next. Nothing was broken. What was missing was
any way to say *this button matters more than that one*, because every
button came out the same weight.

These are the rules, not a style guide. Each one exists because its
absence cost something.

## Colour: the four tokens, never the raw palette

`tailwind.config.js` defines `accent`, `danger`, `warn`, `ok`. Meaning
goes through them — `text-danger`, `bg-warn/15` — so changing a token
reaches every screen. `slate-*` is the neutral ground and is not a
meaning, so it stays as it is.

A chart or canvas that cannot take a class reads the value from a named
constant with the token in its comment (`pages/Capacity.tsx` does this).

## Buttons: weight is the variant's job

One primitive: [src/components/ui/Button.tsx](src/components/ui/Button.tsx).
Never a `<button>` with hand-written classes, and never a new `btnCls`.

| variant | for | how many per region |
|---|---|---|
| `primary` | the one thing this surface is for | one |
| `secondary` | a real action that is not the main one | any |
| `warn` | money, or a wide blast radius | any |
| `danger` | destructive | any |
| `ghost` | navigation, dismissal, a disabled resting state | any |

Two sizes: `sm` (table density, the default) and `md` (standalone).
If a region has no primary, nothing on it is filled — that is correct,
not an omission.

## Type

`text-xs` and `text-sm` carry the console. `text-lg`/`text-xl` are page
and section headings. `text-[11px]` and `text-[10px]` are the sanctioned
dense sub-steps — they appear in tens of places and are part of the
scale, not improvisation.

Monospace (`<code>`) is for machine identifiers only — plan keys,
account ids, Stripe ids. Never for anything a person wrote.

## Icons

`lucide-react`, sized `size-3` beside `text-xs` and `size-4` beside
`text-sm`. Not emoji, and not a text glyph standing in for an icon —
`✓` renders in whatever the font decides and carries no accessible name.
Emoji inside DATA (a customer's own label) is data, and is fine.

## Tables that scroll

A grid taller than a screen scrolls in its own box, not with the page:
`max-h-[70vh] overflow-auto`, `tabIndex={0}`, `role="region"` with a
label, and `overscroll-contain`. Its head sticks to the top (which
column am I in) and its action row sticks to the bottom (what can I do
about it) — both opaque, because rows pass beneath them.

## What this file does not cover yet

Inputs (five identical `inputCls` copies), `Card` (two implementations),
dialogs (two hand-rolled fixed-backdrop overlays with no focus trap,
Escape or `aria-modal`), and the 42 native `title=` tooltips, which are
unthemed and invisible on touch. Each is a real gap; none of them was in
the way of the work that produced this file. Write the rule here when
you close one.
