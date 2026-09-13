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

## Inputs, cards, dialogs

`ui/Input.tsx` exports `INPUT_CLS` and an `Input`; five pages had the
same string character for character. `ui/Card.tsx` is the titled section
with an optional action slot.

`ui/Dialog.tsx` is the only modal. Never a bare `fixed inset-0` — that
is a backdrop, not a dialog: it has no focus trap, so Tab walks out of
it and keeps going through the page underneath, which is still there and
still focusable; no Escape; no `aria-modal`, so a screen reader
announces the page behind it as if the dialog were not there; and no
scroll lock. The primitive does all four and returns focus to whatever
opened it.

## Per-operator state

`lib/prefs.ts`, with the keys declared there and FROZEN — renaming one
throws away that operator's setting silently. Never `localStorage` in a
component. Anything the server acts on is data, not a preference.

## What this file does not cover yet

Two things, both ratcheted in
[tests/test_console_buttons_come_from_one_place.py](../../tests/test_console_buttons_come_from_one_place.py)
so they can only shrink:

- **154 raw palette classes** (`text-rose-300`, `bg-emerald-500/15`)
  where `danger`/`ok`/`warn` exist. Not swept, deliberately: a token is
  one hex value, while `rose-300` on `rose-500/10` is a contrast PAIR,
  and collapsing those across twenty pages nobody has opened in a
  browser trades a naming problem for a legibility one. It wants a
  tone helper (the customer dashboard has `toneClasses`) and a page at
  a time.
- **44 native `title=` tooltips**, unthemed and invisible on touch.
  They want a themed Tooltip primitive that does not exist yet.

`components/AlertRoutingCard.tsx` still has its own `Card` — it is a
feature card with a different anatomy, not a second copy of this one.
