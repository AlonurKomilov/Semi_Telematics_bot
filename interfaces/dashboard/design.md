# 4truck Dashboard — Design System

> **Single source of truth for the customer dashboard's look & feel.**
> Read this before building or changing any UI. The goal is *consistency
> over cleverness*: this is a fleet-operations tool, not a marketing page.
> When a rule here conflicts with a "nicer-looking" one-off, the rule wins.

This document describes what already exists in the code (it is not
aspirational) plus the one layer that was missing — semantic status
tokens. Values live in [`src/index.css`](src/index.css) and
[`tailwind.config.js`](tailwind.config.js); this is the human-readable
contract over them.

Scope: `interfaces/dashboard` (customer app, `dash.4truck.us`). The
operator console (`system_dashboard`) and the Telegram `miniapp` are
**separate design languages by intent** — see [§9](#9-the-other-two-apps).

---

## 1. Principles

1. **Token, never literal.** No `#hex` in components, no raw Tailwind
   palette (`text-green-500`) for meaning. Reach for a token or a helper.
2. **Compose, don't reinvent.** A new screen is existing primitives
   (`ui/*`) and sections arranged — not new buttons/cards from scratch.
3. **Theme-driven.** Colour, radius, and every LENGTH flow from CSS
   variables the user can re-skin from the theme picker. Hardcoding any
   of them breaks that switch silently — see §5.1 for how size works.
4. **Dense and quiet.** `text-xs`/`text-sm` are the body sizes here;
   colour is reserved for *signal* (status), not decoration.

---

## 2. Colour tokens

All colours are CSS variables (oklch) that flip between light `:root`
and `.dark`. Use them through Tailwind classes — `bg-card`,
`text-muted-foreground`, `border-border`, etc. **Never** write the raw
oklch or a hex equivalent.

### Surfaces (elevation ladder)
| Token | Use |
|---|---|
| `background` | The canvas behind everything |
| `sidebar` | Persistent chrome (nav + topbar) — a distinct cooler plane |
| `card` | Standalone content cards |
| `popover` | Menus, dropdowns, dialogs (sits above cards) |
| `muted` | Subtle fills — progress tracks, empty cells, hover |

### Text
| Token | Use |
|---|---|
| `foreground` | Primary text |
| `muted-foreground` | Secondary / labels / metadata |
| `*-foreground` | On-colour text (e.g. `primary-foreground` on `bg-primary`) |

### Declare colour — never inherit it ⭐

**Every element that renders text on its own surface must set an explicit
foreground token.** Don't rely on the inherited `color` — inheritance pulls
whatever some ancestor last set, and that ancestor may not flip with the
theme.

Why this matters (a real bug this rule exists to prevent): `<body>` used to
carry an always-dark splash background, so its text default was historically
a raw near-white. Form controls (`Input`, `Textarea`) declared a `bg` and a
`placeholder:` colour but **no value colour** — so the typed text inherited
near-white. On the **dark** theme (dark field) it was visible; on the
**light** theme (white field) it was white-on-white — invisible. The field
looked empty even though text was there.

A second bug from the same family, and why the token pairs matter: the
`destructive` colour was registered in `tailwind.config.js` **without** a
`foreground` key, so `text-destructive-foreground` — used on five delete /
disconnect buttons — matched no rule and the label fell back to inherited
`--foreground`. That made the label colour an accident of the theme, and
both themes failed AA on it (4.15:1 on light, 3.03:1 on dark). Note the fix
is asymmetric on purpose: light's `--destructive` is dark enough to carry a
near-white label (4.56:1), dark's is light enough to need a near-black one
(6.26:1). **Measure before changing either.**

### The pre-paint theme stamp ⭐

`index.html` opens with an inline `<script id="theme-boot">`. It reads the
stored theme and stamps `<html>` — the `dark` class plus `data-theme` /
`data-radius` and the `--size-*` multipliers — **before the first paint**.
Everything else
about theming depends on it, so it is worth knowing why it exists and what it
may not do.

**Why it can't be React's job.** `ModProvider` applies the theme from an
effect, which runs *after* the first paint, and the module bundle only
executes once it has been fetched and parsed. Whatever paints before that is
governed by the document alone. That gap used to be papered over with
`bg-gray-950` on `<body>` — an always-dark splash. It spared dark-theme users
a white flash and handed light-theme users something worse: nothing ever
removed the class, so the dark canvas stayed under the app permanently and
showed through **every surface that isn't full-viewport** — the boot spinners
in `App.tsx`, the maintenance overlay. (The public-apply branch escaped it
only because `main.tsx` resets `body.className` for its own mount.) With the
stamp in place `<body>` needs no literal at all: `body { background-color:
var(--background) }` resolves correctly in both themes, and the stylesheet is
a render-blocking `<link>`, so it is parsed before anything paints.

**Four rules the script must keep** — `src/test/themeBoot.test.ts` runs the
real script out of `index.html` against the real `applyTheme` and enforces
all four:

1. **Read-only.** `preferences/local.ts` is the single writer of that key and
   owns the legacy copy-forward; a second writer breaks that contract.
2. **Skipped on the apply host** — same predicate as `main.tsx`. That surface
   owns its own theme via `applyPublicFormTheme`.
3. **Hand-written, never build-generated.** Tailwind's content scanner reads
   this file for class names; generated markup is invisible to it.
4. **In step with `applyTheme` and the registry defaults.** The script runs
   before any module exists, so it re-states the enums and defaults as
   literals — a duplication that is only safe because the test compares the
   two on every valid input, on garbage, on nothing, and on the legacy key.

**Verify against a build, never the dev server.** `npm run dev` injects CSS
via JS and so does not reproduce the pre-JS window at all. Use
`npm run build && npm run preview`, with storage both seeded and cleared.

Rules that fall out of this:
- A form control / button / chip / any text-on-surface element **declares
  `text-foreground`** (or the matching `*-foreground`), never inherits.
- A `bg-<surface>` and its `text-<surface>-foreground` travel **together**
  — `bg-popover` ⇒ `text-popover-foreground`, `bg-primary` ⇒
  `text-primary-foreground`. Setting one without the other is the smell.
- **Check both themes.** A colour can be fine in one and invisible (or
  low-contrast) in the other. "Works in dark" is *not* "works" — eyeball
  light **and** dark before calling a surface done.

### Identity & interaction
| Token | Use |
|---|---|
| `primary` | Primary actions; brand accent. Chromatic blue in **both** modes — light's was an achromatic near-black once, and index.css records why that was an oversight, not a decision. The accent presets re-point it |
| `secondary` | Secondary buttons / quiet fills |
| `accent` | Hover/active background for interactive rows |
| `border` / `input` | Hairlines and field outlines |
| `ring` | Focus indicator — **solid** `var(--primary)`, declared once in index.css. Never re-declare it in a stylesheet; an element-scoped tint must set its own (see below) |
| `destructive` | Destructive **actions** (delete buttons). Not for status — use `danger`. |

There is no literal brand ramp. A `brand.50…900` scale of ten hardcoded
hex values used to live in the config — the only hardcoded palette in a
token-driven system — and not one class ever referenced it. Use `primary`.

### Mode and accent are two axes

The theme picker asks two questions, and for a long time one field
answered both. `dark-blue | dark-purple | dark-green | light` reads as a
list of four themes, but three of those entries name a mode AND an accent
while the fourth names only a mode — so Light sat in the row looking like
a kind of dark, and "Light with a green accent" was not merely
unsupported, it was **unsayable**.

Light was never accent-less either. Its `--primary` is
`oklch(0.52 0.2 264)` — chromatic blue. It had an accent all along; the
grammar just had no way to name it.

    stored   { mode: 'dark' | 'light', accent: 'blue' | 'purple' | 'green', radius }
    stamped  <html class="dark" data-accent="green" data-radius="pill">
    CSS      :root[data-accent="green"]  /  .dark[data-accent="green"]

**An accent needs a value per mode.** A `--primary` bright enough to read
on near-black is too pale to clear AA as text on white, so every accent
block exists twice. Blue has no block at all — it IS the base `--primary`
in both modes, the same way `rounded` is the absence of a `--radius`
override. Guard 34 fails if an accent ships with only one half, because
the picker would offer a chip that does nothing in the other mode.

Light green is `oklch(0.48 0.15 142)`, a step darker than its siblings at
0.52, and that is deliberate: `--ok` in light is `oklch(0.52 0.15 150)`,
so a 0.52 green accent sat at ΔE2000 **4.28** from the success colour and
a primary button read as a success state. Dropping the lightness
separates them the way the dark themes already do (0.65 against 0.76) and
lands at 6.59 — better than the blue-against-`--info` pair (6.29) that
has shipped all along.

**The migration lives in `sanitize`, and nowhere else.** That function is
the single funnel for all four read paths — canonical key, legacy key,
cross-tab adopt, and the synced cross-device blob — and it rebuilds the
stored object field by field, dropping anything it does not name. A split
that forgot the migration branch would hand every dark-purple,
dark-green and light user a Dark Blue theme on their next page load, in
silence. `registry.test.ts` covers it directly; `themeBoot.test.ts`
**cannot**, because it compares the pre-paint script against `applyTheme`
and `applyTheme` never touches the sanitiser. Deleting the migration left
that file entirely green, which is why both tests exist.

The pre-paint script in `index.html` carries a second copy of the same
map, because it cannot import (rule 4 above). If the two disagree the
first painted frame is one theme and the app snaps to another after
hydration — `themeBoot.test.ts` compares them for exactly that reason.

`ThemeSetting.color` is kept, derived, and **deprecated**. It exists so a
build without the split can be rolled back to without resetting anyone —
the recipe CLAUDE.md prescribes for a renamed wire value. It cannot
express light + purple/green; those collapse to `light`, keeping the mode
and losing the accent, which is the right half to lose. Delete it, and
the migration branch, one release after the split ships.

### A mod is a look; the axes are still the state ⭐

A **pack** is a colour: an id, a label and a seed per mode, in
[`src/mods/catalogue.ts`](src/mods/catalogue.ts). A **mod** is a named
combination of axes we already have — a pack plus corners plus scale —
and it costs nothing to add: no seed, no CSS block, no swatch, no ramp
rotation.

That split is not tidiness. The first draft put `radius` and `size` onto
the pack, which meant every look needed a hue of its own, and the
palette has none left to give: every remaining hue either collides with
`--danger` under simulated colour blindness (352-2, at dE2000 1.6-3.0),
collides with `--warn` (66-68, at 1.4-2.7), or duplicates a pack we ship
(274-296, at 1.5-7.5). One look, and we would have been out.

**The service lives in `src/mods/`, and application code imports from
`src/mods` — never from a file inside it.** ⭐

**Mods is the umbrella; theme is the COLOUR part inside it — and the
names have to say so.** ⭐ The code said the reverse for a while:
`THEME_MODS` held the mod list, `ThemeMod` was a whole look, and
`ThemeMaterial` / `ThemeMotion` / `ThemeIcons` named three axes with
nothing to do with colour. The proof it confused people was in the tree
— `AppShell` read `theme.entrance`, a page-animation flag, out of a hook
called `useTheme`. The vocabulary is now:

| the whole mod | the colour part |
|---|---|
| `ModPanel` · `Modifications` · `ModControls` | `THEME_PACKS` · `ThemePack` · `packById` |
| `useMods` · `ModProvider` · `ModContext` | `ThemeSeed` · `derivePalette` · `mods/theme/` |
| `MODS` · `Mod` · `ModAxes` · `ModSetting` | `ThemeMode` · `ThemeAccent` · `ThemeColor` |
| `MOD_MATERIALS` · `MOD_MOTIONS` · `MOD_ICONS` · `MOD_RADII` | `applyTheme` (the axes → `<html>` mapping the boot script mirrors) |

`mods/naming.test.ts` enforces it: the barrel may export a
theme-flavoured name ONLY for something on the right-hand column, and
each one is allowlisted **with the reason it really is colour**. A new
`ThemeSomething` fails until it is renamed or justified — which is the
point, because the axis set has grown four times and will again. The
same file asserts `ModMaterial` / `ModMotion` / `ModIcons` are declared
ONCE: they used to be derived off the same arrays in both
`mods/catalogue.ts` and `preferences/registry.ts`, so one type name had
two declarations and resolved differently depending on which barrel you
imported from — identical then, invisible until an array changed.

**Frozen strings are exempt, and stay exempt.** The stored key is
`mods.theme`, the wire blob field is `appearance.default.theme`, and the
DOM alias is `data-theme`. A key is an ADDRESS, not a description;
renaming one costs the four-place ritual for zero clarity and — for a
`synced` key — orphans the server row. See
[preferences/CLAUDE.md](src/preferences/CLAUDE.md).

`theme` stopped being the right word some time ago: it is a colour word,
and what this service holds is colour, corners, scale, material, motion,
icon weight, a page entrance and sound. Mods is the noun that covers
them, and a theme is the colour part inside it — hence `mods/theme` and
`mods/sound`.

    mods/index.ts      the one import surface
    mods/catalogue.ts  MODS, the axes, THEME_PACKS
    mods/context.tsx   useMods, applyTheme
    mods/ModPanel.tsx  ModPanel (top-bar popover) + ModControls
    mods/taxonomy.ts   THE declaration: categories → items → axes
    mods/Modifications.tsx  the /profile card
    mods/page/         the /mods page: hub → category → item, from the taxonomy
    mods/href.ts       MODS_HREF (the card) and MODS_PAGE_HREF (the page)
    mods/SizeCard.tsx  size, whole
    mods/inject.ts     installing token values
    mods/theme/        palette · contrast
    mods/sound/        engine · useCue
    mods/icons/        IconWeight

### A popover and a profile card are one component, not two ⭐

**Three surfaces, one store.** Mods is the ENGINE; its data lives in
the person's preferences and every surface reads the same rows. The
surfaces differ in SHAPE, and that is GX's own split:

- the top-bar **popover** — quick: the mod, the colour, the size, a door;
- the **`Modifications` card on `/profile`** — the flat list, for
  somebody who knows what they want;
- the **`/mods` page** — the same settings drawn as depth: a hub of
  categories with how far each is dialled, a category as a grid of
  items each saying whether it has been touched, an item as its
  control. `/mods/:category/:item` carries the level in the URL.

`/mods` was deleted once, on the reasoning that a page is a CATALOGUE
and there was nothing to browse. That read GX wrong: its page is where
you see what you *have*; the store is a button on it. The page renders
FROM `mods/taxonomy.ts` — every tile and route segment comes from the
one declaration — so it is not a fourth copy of the category shape.
Its item level renders the card's own `Section`, so the two cannot
answer differently.

The popover and the card render the SAME `ModControls`, switched by
a `compact` flag; the page's item level renders the card's `Section`. Never
two copies of one chip row: duplicated JSX drifts, the two surfaces
answer the same question differently within a release, and nothing
fails. What differs between them is which branch renders and one type
step (the popover's caps label runs `text-2xs font-semibold` because
seven of them stack inside `w-56`; the page uses §4's canonical
`text-xs font-medium`).

**A component that IS a Card is never rendered inside a Card.** ⭐
`SizeCard` owns its own `<Card id="interface-size">` and its pinned
styles; the /mods item level wrapped it in a second one and got a box
inside a box — and the anchor id mounted twice, which a `#hash` link
resolves to whichever came first. A wrapper that needs a box asks the
child whether it already is one; the two live cases are `SizeCard` and
`Modifications` itself.

**Stacking chrome belongs to the container, not the section.** ⭐
`Section` carries `border-t border-border pt-4 max-w-2xl` because on the
profile card it stacks under the mod row and its siblings. Alone in its
own card that rule has nothing above it and the column cap leaves a
third of the card empty. A section that can render alone takes a
`standalone` prop rather than a second copy — same section, no chrome of
its own. The general rule: if a class exists only because of what sits
ABOVE a component, it is the parent's to apply.

**The split, and the one axis deliberately on both sides.** The popover
holds what changes often — which mod is installed, the colour it wears,
the global size — plus a door to the rest. Corners, material, motion,
sound and sizing BY REGION are settings rather than toggles, and §7
forbids inventing an in-between popover width, so they are page-only.
Size is the exception that proves it: the popover keeps the global
slider because "a bit bigger" is the case almost everyone wants, and
the page does NOT repeat it — there `SizeCard` owns size whole (global,
per region, cross-device). One object, one face per surface;
`mods/modControls.test.tsx` renders both branches and fails if the page
grows a second global slider or the popover loses its door.

**Mods is not in `routeRegistry`/`featureCatalog`.** Those drive the
operational sidebar; a customization surface listed beside Vehicles and
Loads reads as work. It rides `/profile` — personal, every
authenticated user, no permission — and is reached from the top-bar
palette button and from the avatar menu, both of which point at the one
`MODS_HREF` constant.

**Installing a mod is undoable.** One click overwrites accent, corners,
material, motion, icon weight, size and sound, and "let me just see
what Wall looks like" is the most likely reason anyone clicks. It goes
through `undoableAction`, the same helper that guards SizeCard's reset.

**Each card resets what it owns, and nothing else.** "Reset appearance"
sits on the Appearance card, "Reset sizes" on SizeCard; neither reaches
into the other. Two axes are deliberately outside both: `mode`, because
dark/light is about the room the person is sitting in rather than the
look — it is the one axis a mod may never carry, and a reset that threw
a light-mode user into dark would be that mistake arriving from the
other direction; and the derived `color` alias, which has no independent
value. `mods/resetAppearance.test.tsx` asserts every field of
`THEME_DEFAULT` is either reset or named in the exclusion list, so
adding an axis forces the decision — the same shape `PREPAINT_AXES`
uses, and for the same reason.

**Both cards under `Modifications` wear one chrome.** Title, muted line
and reset come from `SectionHeader`'s own `description` and `action`
props, never a hand-rolled flex row. Their BODIES may differ — six
parallel chip groups is not one slider plus a disclosure — and forcing
one body grammar onto both would be cargo-culting; the header is where
the card says the two are siblings.

Two files may reach past the barrel, and `mods/index.test.ts` fails if a
third appears without a stated reason: `preferences/registry.ts`, which
sits UNDERNEATH the service (`mods/context` imports the preferences
store, so a barrel import would close a cycle), and
`test/themeBoot.test.ts`, which compares the pre-paint script against
`applyTheme` itself rather than whatever the barrel re-exports.

The stored keys carry the same namespace: `mods.theme`,
`mods.sound.pack`, `mods.sound.volume`. Each lists its previous spelling
in `legacyKeys`, which `readPref` reads verbatim, sanitises, and copies
forward — so an existing browser keeps its appearance and lands on the
canonical key at the next read. The pre-paint script chains all three
names for the same reason. The preferences SERVICE was not touched: it
holds 45 keys across many features and is not a mods thing.

`dispatch.soundOn` is deliberately NOT part of this service. It is the
live alerts feature's own gate and belongs to the feature that uses it —
the sound service sets the level and the pack, never whether a
particular feature speaks.

**A mod is INSTALLED, and its identity is stored.** ⭐

This replaced an earlier rule that read well and had a wall in it:
"applying a mod writes the axes and then forgets", with `activeModId()`
recomputing identity each render. That is elegant while everything a mod
carries is a value on `<html>` — and it fails the moment one is not. A
sound pack cannot be read back off the DOM. Neither can a wallpaper. So
identity-as-a-sum would have stopped a mod's sounds the instant somebody
nudged the corners, which is not what installed means anywhere.

Identity is therefore `theme.mod`, stored, and it survives an edit.
`modMatchesAxes()` answers the smaller question it used to answer by
accident: whether what you see is still exactly what the mod asked for.
The panel shows that as "edited — tap again to restore", and tapping the
installed chip re-applies its axes.

Two consequences worth stating:

- **Building a look by hand is not installing it.** Someone can set
  every axis Cab sets without ever tapping Cab. Matching says yes;
  installed says no; and once mods carry assets only the second may
  start anything.
- **Only what a mod DECLARES can be departed from.** Wall says nothing
  about material, so choosing glass has not edited Wall — it answered a
  question Wall left open.

**A mod never carries `mode`.** Dark or light is about the room a person
is sitting in — a bright yard office at noon, a cab at 2am — and a look
that seizes it makes the screen unreadable for exactly the reason they
chose the other one. `themePacks.test.ts` asserts the field's absence
structurally, so it cannot be added back by someone reading the field
list without the reason beside it.

**Size floors at 1, not at `SIZE_MIN`.** The panel's slider starts at
100% because everything below waits on the 24px hit-target floor (§5.1),
and a mod that set 0.9 would put the app somewhere its own control
cannot bring it back from.

This is the axis Opera GX cannot distribute: a GX mod carries colours,
sounds and wallpapers, while corner radius and density stay user
settings, unreachable from a mod. Ours are already values on `<html>` —
`data-radius` and the `--size-*` multipliers — so a mod just carries
them.

### Rules this layer enforces

**Never write `text-white` on a tone fill.** Use `text-ok-foreground`,
`text-warn-foreground`, `text-danger-foreground`, `text-info-foreground`;
they flip with the theme. This is measured, not taste: the dark themes'
tones are LIGHT (`--warn` is `oklch(0.82 …)`), so white-on-warn scored
**1.77:1** and no tone beat 2.91:1 — a badge nobody could read. The same
fills take near-black at 6.8–11.2:1. Light theme keeps white (5.1–6.2:1).

**Never re-declare `--ring` in a stylesheet.** It is `var(--primary)`,
so a new accent preset gets a correct ring for free.

It is **solid**, and that took three tries. WCAG 1.4.11 wants 3.0 for a
focus indicator. At the inherited 50% it measured 2.21 / 2.06 / 2.02 /
2.37 — failing in every theme. 72% cleared 3.0 against `--background`
but not against `--card`, which is lighter: dark blue landed at 2.67 and
dark purple at 2.59, so focus was unreadable on exactly the surface most
focusable things sit on. The alpha that clears both grounds in all six
cells is 84%, which is indistinguishable from opaque — so it is opaque.
The "glow, not a block" intent lives in `ring-ring/50`, the soft 3px
halo, which is decorative; the indicator 1.4.11 is about is
`focus-visible:border-ring`. **A control with the halo and no
`border-ring` has no compliant focus ring** — `slider.tsx` and
`kpi/dispatch/board/DayCells.tsx` are both in that position today.

**An element-scoped tint is the exception, and it is forced.** A custom
property's `var()` is substituted when that property is computed *on the
element that declares it* — `--ring` on `:root` resolves against
`:root`'s `--primary`, and descendants inherit the already-resolved
value. An element that re-points `--primary` (the public apply form
tinting itself to a carrier's brand colour, on a form-root `<div>`) does
**not** move the ring and must set its own. Deleting that as a
"duplicate" put a 4truck-blue focus ring on every field of a fully
brand-tinted page.

**`--primary-foreground` inverts with the mode**, for the same measured
reason the tone foregrounds do: light's `--primary` is 0.52 and dark's is
0.62–0.65, so one is a dark fill and the other a bright one. Near-white
on the dark fills scored 3.65 / 3.81 / **2.89** — that is the primary
button label, below AA on all three dark themes and below even the 3.0
floor on green.

**The accent as TEXT is a different colour from the accent as a FILL.**
`--primary` has to be bright enough to read on the near-black canvas,
which makes it too pale to clear AA as text on the lighter surfaces
stacked above it — `text-primary` measured 3.33:1 on `--popover` and
3.89:1 on `--card` in dark blue, across ~270 call sites including the link
variant of both primitives. One value cannot serve both jobs; that
overload is the shape of defect this whole layer kept producing.

So `--primary-text` exists, per mode and per accent, and
`tailwind.config.js` overrides `textColor.primary` to point at it. **No
call site changed** — `text-primary` and `bg-primary` simply stop being
the same colour, which is the honest answer to them never having been
the same job. `text-primary-foreground` has to be restated in that
override or it would be lost with the key it lives under.

It lands close to `--primary-hover`, and they are still two tokens on
purpose: fold them and a hover retune silently moves every piece of body
text.

**`--destructive` splits the same way.** Both primitives ship
`bg-destructive/10 text-destructive`, so the token is read ON a wash of
itself and measured 3.31–3.98 in light, 3.13–3.98 in dark.
`--destructive-text` exists for the same reason `--primary-text` does;
the solid destructive button keeps the saturated `--destructive`.

**Where both roles want the same direction, retune instead of splitting.**
That is the test for which fix a token needs, and four tones passed it:
`--ok` and `--info` in light went darker, `--danger` and `--info` in dark
went lighter, and in every case the move that fixed the soft pill also
strengthened the solid fill's label. A split would have been four more
tokens for nothing. `--primary` and `--destructive` failed that test —
brightening the fill to fix the text pales the brand — which is why only
those two are split.

**The tone wash is 12%, not 15%.** The soft pill is the tone as text on a
wash of itself, so the wash is half of that pair. On `--popover` — the
lightest surface either theme has — dark danger sat at 3.58, and clearing
it inside `--danger` would have taken its chroma from 0.19 to 0.125. A
danger colour's job is to be noticed, so the wash gave way instead. All
four move together; the 30% `-bd` border keeps the pill's shape at the
lower fill.

**`--muted-foreground` is 0.545, and the blast radius is the argument for
it.** At 0.556 it read 4.35 on the app's own `--muted` surface and 4.74
on white. Darkening lifts both, across roughly 2187 call sites — the rare
retune where touching everything is the point.

**Hover is a token, not `bg-primary/90`.** An alpha fade blends toward
whatever is *behind* the button, so the same class darkens it on a dark
canvas and lightens it on a light one. On dark that dragged the label
from 5.19:1 to 3.60:1 — hovering a control is not supposed to make it
harder to read. `--primary-hover` moves the right way in each mode
(darker in light, lighter in dark) and every accent block carries one;
guard 34 fails if an accent re-points `--primary` and leaves its hover
behind, because the button would then jump to the *blue* hover on
pointer-over.

**Anything the dark themes override, `@media print` must put back.** A
dark-mode user otherwise prints white text on a background the printer
drops: a blank page. The print block is a copy of the light values and
guard 31 asserts set-equality both ways plus byte-equal values, which is
the only thing that makes keeping a copy safe.

**Tailwind 3 syntax only.** The primitives were pasted from shadcn's v4
era and thirty classes across select, dialog, sheet, table, tooltip,
avatar, button and badge compiled to *nothing*: `data-open:`,
`data-closed:`, `**:`, `*:[a]:`, `has-data-[…]:`, `not-data-[…]:`,
`data-starting-style:`, `[a]:`, `in-data-[…]:`. Write `data-[open]:`,
`[&_*]:`, `[&>a]:`, `has-[[data-…]]:`, `[a&]:`, `[[data-…]_&]:`.

It is not only variants. A **step the config never defined** is just as
dead: `backdrop-blur-xs` (v3's smallest is `-sm`) meant every dialog and
sheet shipped without the blur it asked for; `max-w-55` was missing
because `EXTRA_STEPS` reached every dimension key except `maxWidth`, so
two `truncate`s had no box to clip against; `underline-offset-3` is not
on the 0/1/2/4/8 scale; `field-sizing-content` is v4-only, so six
textareas never grew.

A class Tailwind never emits produces no error and no warning — the
element just renders unstyled, and it reads as a design choice. Guard 32
compiles every class the codebase writes, bare and prefixed, and fails on
any that vanishes. `aria-invalid:` needed a config entry
(`theme.extend.aria`) and without it every invalid field drew no error
ring at all.

**Animations come from `tailwindcss-animate`, the v3 plugin.** Not from
`tw-animate-css`, which is the Tailwind 4 package: it ships 54 `@utility`
blocks, v3 has no such at-rule, so it passed them through untouched and
the browser dropped every one. `animate-in`, `fade-in-0`, `zoom-in-95`
and `slide-in-from-*` existed only as keyframes with no classes to fire
them, and 43 usages animated nothing.

**A colour we did not author is DERIVED and CLAMPED, never accepted.** ⭐

Two surfaces already take a colour from outside — the public apply form
takes a carrier's brand and surface hex, and an account theme will take
more. For those, the rule is not "validate it and show an error if it
fails". It is: take the seed, derive every dependent token, and pull
each one to a contrast floor against the ground it is actually read on.
A validator leaves someone to handle "no". A clamp has no failure
branch, needs no error copy, and cannot be overruled by a customer who
prefers their brand green to being read.

The arithmetic lives in `src/mods/theme/contrast.ts` and it SHIPS — the same
module the build-time colour guards import, so a value proved legible by
`colour.test.ts` is computed the same way in the browser. Do not write a
second copy; there have been three.

Three things it settles, all measured:

- **At AA a clamp cannot fail.** Chroma falls to zero at both ends of
  lightness, so the endpoints are pure black and white for every hue,
  and the worst possible ground (relative luminance 0.17913) still
  yields 4.5826:1. Legibility is always reachable. Above 4.58 — AAA —
  it is not.
- **Choose direction by measuring, never by lightness.** OKLab lightness
  and WCAG luminance disagree on saturated hues: `#0048f8` has an OKLab
  L of 0.505, so a proxy calls it light and darkens the text — 3.28:1,
  where lightening gives 6.40. A brand blue is not an edge case.
- **Clamp against the ground that ships, at 8 bits.** Secondary text
  clamped against an unrounded float, then emitted as hex, came out
  just under AA on a fifth of surfaces. The guarantee has to be about
  the value the screen paints.

Floors are OUR values, not the standard's maximum. `--border` and
`--input` clamp to **1.26:1** because that is what our own light theme
ships; holding a public page to WCAG 1.4.11's 3:1 while the dashboard
sits at 1.26 makes them look like two products. Both move together when
the boundary redesign happens.

**Vendor surfaces need explicit colour.** Sonner defaults to a light
toast and Leaflet hardcodes `background: white` on popups and tooltips,
so on the dark themes both were white cards in a near-black page. Both
now take `--popover` / an explicit `theme` prop. `color-scheme` is
declared per theme so scrollbars and form controls follow too.

---

## 3. Status tokens — the meaning layer ⭐ (the new piece)

Four semantic hues answer "what does this state mean". They are the
**only** correct way to colour a status, severity, or health signal.

| Tone | Token | Means | Example states |
|---|---|---|---|
| 🟢 ok | `--ok` | good / running / done | moving, active, completed, paid, healthy |
| 🟡 warn | `--warn` | attention / pending | idle, pending, high-priority, degraded |
| 🔴 danger | `--danger` | bad / urgent | stopped, overdue, critical, failed |
| 🔵 info | `--info` | in-progress / neutral-active | in_progress, scheduled, medium |
| ⚪ neutral | `muted` + `border` | no signal | off, cancelled, draft, unknown |

These flip light↔dark automatically (dark green text on white →
light green text on black) — one class, both modes.

### The soft-pill recipe
Every badge/chip uses one treatment: a **15% tinted fill + solid text +
30% border**, in one hue. Each tone exposes three tokens for this:
`text-<tone>` (solid), `bg-<tone>-bg` (fill), `border-<tone>-bd` (border).

**Geometry is part of the recipe too** — the reference is
[`StatusBadge`](src/components/StatusBadge.tsx): **`rounded-md · px-2
py-0.5 · text-xs font-medium`**. One corner shape everywhere; a status
pill is never `rounded-full` on one page and square-ish on another.

**Shape is the grammar, and it says one thing: a CAPSULE
(`rounded-full`) states a FACT you read; a bordered rounded rectangle
invites a CLICK.** The rule was already written down in
`features/alerts/_shared/components.tsx`, and the rest of the app
already followed it — a census found twelve static capsules against two
clickable ones. The two exceptions were converted (owner decision,
2026-08-26): Scorecards' pillar filter and the AI composer's suggestion
chip. It matters most at **Sharp**, where a rounded rect is a true
right angle and a capsule stays a capsule — the setting that makes the grammar
loudest is the one that exposes a broken one.
(Dense table cells may drop to `text-2xs`, keeping the rest.)  Close ✕
icons follow the same two-step idea: `12` inside chips/tags, `16` in
modal/panel headers.

> The `-bg`/`-bd` tokens pre-bake the alpha (via `color-mix` in
> [index.css](src/index.css)) so the recipe is one short class set.
> `bg-ok/15` etc. **also** work — the `/<alpha>` modifier is enabled on
> every token by `tokenColor()` in [tailwind.config.js](tailwind.config.js)
> — but prefer `toneClasses()` / the `-bg`/`-bd` tokens so the soft-pill
> stays identical everywhere.

Don't hand-write the recipe — call the helper in
[`src/lib/status.ts`](src/lib/status.ts):

```tsx
import { statusClasses, statusTone, toneClasses, toneText } from '@/lib/status';

// From a domain string (most common):
<Badge variant="outline" className={statusClasses(task.status)}>{task.status}</Badge>

// From a known tone:
<span className={toneClasses('danger')}>Overdue</span>

// Just the foreground (icons, dots, chart bars):
<AlertCircle className={toneText('warn')} />
```

`statusTone()` owns the string→tone mapping so the same status can't
render green in the table and amber in the badge. **Add new statuses to
that map — never pick a colour at the call-site.**

> ❌ `bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30`
> ✅ `toneClasses('ok')`

---

## 4. Typography

- **Font:** Geist Variable (`--font-sans`). Don't import another family.
- **Scale (what's actually used):**

| Class | Size | Role |
|---|---|---|
| `text-3xs` | 10px | Densest micro-labels — chip captions, axis hints, sparkline notes |
| `text-2xs` | 11px | Dense metadata — table sub-text, timestamps, secondary chips |
| `text-xs` | 12px | Dominant body / table cells / badges / metadata |
| `text-sm` | 14px | Default body, form labels, buttons |
| `text-base` | 16px | Emphasised body |
| `text-lg` / `text-xl` | 18/20px | Section / card titles |
| `text-2xl`+ | 24px+ | Page titles, big KPI numbers only |

- **No arbitrary sizes.** Use the scale — including `text-2xs` / `text-3xs`
  for sub-12px text. **Never** `text-[10px]` / `text-[11px]` / `text-[9px]`:
  those re-type the same value inconsistently. (`text-3xs` also covers the
  rare 8–9px cases.) Charts/SVG that need a numeric `fontSize` are the only
  exception.
- **Weights:** `font-medium` (default emphasis), `font-semibold`
  (headings), `font-bold` (KPI figures). Avoid `font-light`/`thin`.

### Roles — pick by role, not by eye

The scale above is the vocabulary; these are the **fixed combinations** for
each semantic role. A heading is the SAME size+weight on every page — don't
improvise (that's why some pages used to look heavier/lighter than others).

| Role | Classes | Where |
|---|---|---|
| Page title | `text-2xl font-bold` | top of page — **use `PageHeader`**, don't hand-roll |
| Section title | `text-lg font-semibold` | heading of a major section / card |
| Card / subsection title | `text-base font-semibold` | smaller card or sub-block heading |
| Section label (caps) | `text-xs font-medium uppercase tracking-wide text-muted-foreground` | small-caps group labels |
| Body / table cell | `text-sm` (`text-xs` in dense tables) | default running text |
| Caption / meta | `text-xs text-muted-foreground` | timestamps, secondary metadata |
| Code / ID | `font-mono text-xs` | **only** technical identifiers — record IDs, IPs, hashes, tokens, code snippets |

- **`font-mono` is for machine identifiers only.** Human-readable data —
  company codes, vehicle names, statuses, labels — uses the normal sans
  font (the default `DataGrid` cell). Don't `font-mono` a Company column
  on one page and leave it plain on another.
- **Same column, same render.** When the same logical column (Company,
  status, a count) appears on multiple pages, style it identically — lift
  the renderer to a shared helper rather than re-styling per page.

---

## 5. Spacing — 4px rhythm

Stay on Tailwind's 4px step scale. The house values, in order of
frequency: **gap** `1 / 1.5 / 2 / 3`; **padding-x** `2 / 2.5 / 3 / 4`;
**padding-y** `0.5 / 1 / 1.5 / 2`. Card padding is the `<Card>`
primitive's, not a per-site choice: `compact` (p-3) · `default` (p-4)
· `panel` (p-6, for a centred box that IS the page — auth, blockers,
success screens) · `none` (children own their edges). Radius is not a
variant — see §6.

- **No arbitrary spacing** (`p-[13px]`, `h-[42px]`) for layout. If a
  value isn't on the scale, it's almost always wrong. (Exceptions:
  pixel-exact needs like map overlays, fixed media dimensions.)
- **Keep writing `p-4`.** Every step on this scale is already multiplied
  by the user's Size setting — see §5.1. (This bullet used to point at
  `--spacing-card` / `--spacing-row`; those tokens were read by nothing,
  which is why the old Density picker moved zero pixels. They are gone.)

### 5.1 Size — the four multipliers ⭐

Every length Tailwind emits is `calc(step × var(--size-axis, 1))`. The
user's Size control writes those variables; nothing else changes. **You
keep writing `p-4`, `h-8`, `text-sm` exactly as before** — the class name
is the pointer, the multiplier is applied for you.

| Axis | Rides on | What it moves |
|---|---|---|
| `--size-text` | `fontSize`, `lineHeight` | type and its line box |
| `--size-control` | `w`/`h`/`size`/`min`/`max` ≤ 3rem | things a finger or cursor aims at |
| `--size-layout` | `padding`, `margin`, `gap`, `space`, `inset`, `translate`, `scroll*`, and dimensions 3–6rem | breathing room, and fixed content columns |
| `--size-panel` | dimensions > 6rem, `max-w-*` | menus, drawers, dialogs, panels |

The assignment tables are generated in
[`tailwind.config.js`](tailwind.config.js) from Tailwind's own defaults —
the axis rule is written once there, not as ~600 hand-maintained
formulas.

**Rules:**

- ❌ **Never extend `spacing`.** It is the shared default every dimension
  key derives from, so extending it moves padding, gap, margin, width,
  height and size at once and collapses the four axes into one. Extend
  the derived keys instead. (Verified: extending `padding` moves `p-*`
  alone and leaves `gap-3` / `w-3` / `h-3` untouched.)
- ❌ **No arbitrary lengths** (`h-[220px]`, `p-[3px]`). They are the one
  thing the multipliers cannot reach — an arbitrary value is a promise
  that the element will never follow the user's setting.
- ✅ **`min-h-tap` / `min-w-tap` — the one sanctioned non-scaling step.**
  A pointer target may not shrink below **24×24 CSS px** (WCAG 2.5.8 AA),
  and a floor expressed on the Size ladder is not a floor: it shrinks with
  everything else and stops protecting exactly when it is needed. Measured
  — the house `p-1 -m-1` invisible-padding idiom gives 24px at 1× and only
  **22px at 0.75×**, and `min-h-6` compiles to
  `calc(1.5rem * var(--size-control, 1))`. So every interactive control
  carries `min-h-tap` (plus `min-w-tap` when it is square or icon-only).
  It costs nothing at the default multiplier — the minimum equals the
  height already declared beside it — and it is what lets the Size floor
  drop below 100% at all. Do **not** spell it `min-h-[24px]`: a named step
  is greppable, and an arbitrary value silently emits no rule at all if
  the scanner never sees the literal.
- ⚠️ **Never subtract the shell frame in a `calc(100vh − Nrem)`.** That
  figure encodes today's padding, so it is wrong the moment Size moves —
  and two of the three that existed were already wrong at 1×. Use
  `h-full`, or `flex-1 min-h-0` inside a flex column.
- ⚠️ **A pixel constant standing in for a rendered height is now a bug.**
  `DrillDialog`'s sticky-header reservation is the worked example: it was
  `30`, justified by "one line of text at a fixed size", and that
  justification no longer holds. It is CSS now, riding the same two axes
  as the header it reserves for.
- **Per-component sizing is free.** `--size-*` inherits, so setting one
  on any wrapper scales everything inside it — no context, no props.
- **Regions are a THIRD factor, applied at the point of use.** Every
  length is `calc(step × var(--size-<axis>, 1) × var(--size-region, 1))`,
  and a surface claims a region by spreading `sizeRegion('tables')`
  ([`src/lib/sizeRegion.ts`](src/lib/sizeRegion.ts)) onto the root it
  already renders. Measured: unset costs nothing (identical boxes inside
  and out); global 1.2 × region 1.4 gives 26.88px from a 16px step, i.e.
  it MULTIPLIES — the nested-fallback form
  `var(--region, var(--global))` would have replaced the global and
  silently thrown 1.2 away. `min-h-tap` still measures 24.0px inside a
  region shrunk to 0.85.

  It is a style, not a wrapper component, because the surfaces that own
  a region are already flex parents and scroll containers, and slipping
  a `<div>` in between is how a layout loses `flex-1` or `min-h-0`.

  Nesting REPLACES rather than compounds — an overlay opened from inside
  the assistant renders at the overlay scale, not at both. A region names
  where you are, and you are in one place at a time.
- **The floor is 0.85, and it is a TYPE limit, not a target limit.** It
  was 1.0 while pointer targets had no floor of their own; 193 of them
  were measured under 24px in Chrome and given `min-h-tap`, which does
  not scale, so hit areas now hold all the way down. What stops the
  slider at 0.85 is legibility: `text-3xs` (10px) renders 8.5px there,
  and Geist's stem is one device pixel at 11.63px, so below that the
  dominant `text-xs` stops landing on whole pixels at DPR 1. Going lower
  needs a floor on the small type steps first. The clamp is written
  twice, in `preferences/registry.ts` and in the pre-paint script;
  `themeBoot.test.ts` asserts the two agree.

---

## 6. Radius

**What each step actually renders.** Keep this to hand — every corner
question in this app is a lookup, and the audit that produced these
numbers measured them in Chrome rather than deriving them.

| class | formula | Sharp | Rounded | Pill |
|---|---|---:|---:|---:|
| `rounded-none` | `0` | 0 | 0 | 0 |
| `rounded-sm` | `max(0, r−4)` | 0 | 6 | 12 |
| `rounded` | `max(0, r−3)` | 0 | 7 | 13 |
| `rounded-md` | `max(0, r−2)` | 0 | 8 | 14 |
| `rounded-lg` | `r` | **0** | **10** | **16** |
| `rounded-xl` | `r+4` | 4 | 14 | 20 |
| `rounded-2xl` | `r+8` | 8 | 18 | 24 |
| `rounded-3xl` | `r+16` | 16 | 26 | 32 |
| `rounded-full` | `9999px` | — | — | — |

Three properties fall out of it and each has bitten someone:

- **The move is additive, not proportional.** Every step shifts a constant
  +6px per click. So step RATIOS change wildly — `sm:lg` is 0:0 at Sharp
  and 12:16 at Pill — and a layout that depends on two steps differing
  will not survive the range.
- **`xl` is `r+4`, so it never fits inside `lg` at any preset.** That is
  not a Pill problem; it is a constant 4px overshoot. If a child sits
  inside a `rounded-lg` box, it cannot be `rounded-xl`.
- **At Sharp the bottom four steps are all 0.** `rounded-sm` and
  `rounded-none` are pixel-identical there, which is why a shape that
  must stay curved uses `rounded-full`, not a small step.

One variable, `--radius` (default `0.625rem`), drives everything via the
`rounded-*` scale. The theme picker's Corners preset (Sharp / Rounded /
pill) reshapes the whole UI from it.

- Use `rounded`, `rounded-md`, `rounded-lg`, `rounded-xl` — they all
  track `--radius`. `rounded-lg` is the card default.
- `rounded-full` and `rounded-none` are shapes, not softness — leave
  them. `rounded-full` is for something CIRCULAR by geometry (a dot, an
  avatar, a spinner), something CAPSULE by geometry (a switch track, a
  scrollbar thumb), or a counter that must be a circle at one digit and
  a capsule at two. It is **not** a way to say "very round", and it is
  not available to a control — see §3: a capsule states a fact. (This
  line used to list "pills" and contradicted §3 outright.)
- **Never** hardcode `rounded-[10px]` or use `rounded-4xl` (it ignores
  the user's Corners setting — see the StatusBadge fix for why).
- **JS-drawn geometry** (SVG paths, canvas arcs, a recharts bar) is bound
  by the same rule: a radius baked into a path number is invisible to the
  Corners preset. **Use [`lib/radius.ts`](src/lib/radius.ts)** —
  `useRadiusPx()` reads the live token and re-reads when `<html>`'s theme
  attributes change, which a CSS-var change alone never does because it
  re-renders no React. `clampRadius(r, …dimensions)` is the other half:
  a corner may never eat the shape it is rounding, so a 6px bar with a
  16px corner is a lozenge and its height stops being readable.
  [`components/charts/RoundedBar.tsx`](src/components/charts/RoundedBar.tsx)
  is the worked example for recharts, which takes a NUMBER — pass it as
  `shape`, never `radius`, because `radius` is one static tuple for a
  whole series and cannot know how thick any single bar turned out.
  (This paragraph used to describe the `getComputedStyle` +
  MutationObserver dance and point at `SegmentTab` as the reference. The
  helper is that code, lifted; do not write it a third time.)
- **No radius caps on small variants.** Upstream shadcn ships `xs`/`sm`
  button and select sizes with `rounded-[min(var(--radius-md),10px)]`
  — a cap that silently ignores the Pill preset and makes a small
  button rounder than its neighbouring select. We removed those caps
  from `ui/button.tsx` / `ui/select.tsx` (all sizes now track
  `--radius`); don't reintroduce them when refreshing primitives from
  upstream.

---

## 6.1 Motion — an axis, with one thing deliberately off it ⭐

Every duration and easing in the app is
`calc(<literal> * var(--motion-scale, 1))`, wired once in
`tailwind.config.js` through `scaleMotion()`. Nothing else changed:
there are 189 `transition-*` utilities and only 9 that name a duration,
so almost everything rides the default and one token reaches it. The
easing is `var(--motion-ease, …)` on the same principle.

**The infinite loops are NOT on the axis, and that is the whole point.**
`animate-spin` compiles to `animation: spin 1s linear infinite` — the
duration lives in the shorthand, which `animationDuration` never
touches. Put them on the axis and a "snappy" setting turns 89 spinners
and 18 pulses into a strobe. `theme.extend` must therefore never gain an
`animation` or `keyframes` key; `motion.test.ts` fails if it does.

**Reduced motion ends the loop, it does not speed it up.** The floor is
a `@media (prefers-reduced-motion: reduce)` block, and
`animation-iteration-count: 1` is its load-bearing line — scaling an
infinite animation toward zero is the same motion, faster. It is also
the **only `!important` in the stylesheet**, deliberately: a guarantee
to someone whose vestibular system is at stake is not a preference for a
mod or an inline style to outrank. The guard asserts both that it is
there and that it is still the only one.

The two named settings are multipliers rather than sets of literals
(`calm` 1.6, `snappy` 0.6), so a mod can dial any value the injector
accepts instead of choosing among the three we happened to write.

**Typography joined the token layer at the same time.** `--font-mono`
now exists and `tailwind.config.js` points `mono` at it, so all 106
`font-mono` sites follow a token without changing their class. `sans`
was added for the opposite reason — no site writes `font-sans` today,
and the key stops the first one that does from bypassing the family the
whole document inherits from `html`.

A mod cannot yet CARRY a font, and that is not an oversight: there is no
runtime font loader, and `colWidths` is a synced map of absolute pixel
widths snapshotted from live DOM measurement, so a different font's
metrics would make every saved column wrong on every device with no
invalidation hook.

---

## 7. Components

Build from the canonical primitives in
[`src/components/ui/`](src/components/ui/) (shadcn / base-ui). Don't
re-implement a button, badge, dialog, table, etc.

**Button** ([`ui/button.tsx`](src/components/ui/button.tsx)) — variants:
`default · outline · secondary · ghost · destructive · link`; sizes:
`xs · sm · default · lg · icon*`. Pick a variant; don't restyle with
ad-hoc classes.

**Badge** ([`ui/badge.tsx`](src/components/ui/badge.tsx)) — variants:
`default · secondary · destructive · outline · ghost · link`. For a
*status* badge use `variant="outline"` + `statusClasses()` (see
[`StatusBadge.tsx`](src/components/StatusBadge.tsx) as the reference).

**Shell** ([`src/components/shell/`](src/components/shell/)) — page
scaffolding: `PageHeader`, `KpiCard`, `EmptyState`, `ErrorState`,
`LoadingSkeleton`, `FilterBar`, `Breadcrumb`, toasts. Use these for
headers/empty/error/loading states — don't roll your own.

### View controls vs actions — separate the classes ⭐

A control that changes **what you are looking at** (a date range, a
scope switch, a mode) must not sit in the same visual group, at the same
weight, as controls that **do something** (create, book, delete). Both
render as bordered buttons in the header, so when they run together the
only way to tell a filter from an action is to read all of them —
and the filter is the one you touch most.

Put the view control first, then a divider
(`<span aria-hidden className="h-5 w-px bg-border mx-1" />`), then the
actions. Where a grid owns the constraint (search, column filters, sort)
it already renders its own removable chips — this rule is for the
PAGE-level constraints a grid cannot see. `features/loads/Loads.tsx` is
the worked example.

A view control also has a wider audience than an action: reading a
narrower slice is not a management act, so it renders for every viewer
even when the actions beside it are permission-gated.

### Empty states name the constraint that emptied them ⭐

"No loads in this status." is FALSE whenever a date window, scope, or
page-level filter is also narrowing the view — there may be forty, one
widening away. An empty state that states only the obvious condition
sends someone hunting an old record away believing it is gone.

Say what is filtering, and name the remedy:

```tsx
emptyMessage={rangeDays > 0
  ? 'No loads in this status in the selected date range. Widen the range to see older loads.'
  : 'No loads in this status.'}
```

Reports does the same for its own window ("Try a different tab or widen
the date range"). This applies to every constraint the grid cannot
render as a chip, because those are exactly the ones the reader cannot
see.

### Control & overlay sizing

Sizes are scales, exactly like colours are tokens — a control or overlay
picks a step, never invents a value.

- **Control heights** come from the Button primitive's ladder and apply to
  every interactive control (hand-rolled included):
  `h-7` (sm / dense toolbars) · **`h-8` (default)** · `h-9` (lg / hero
  actions). Square icon-buttons are the same steps: `size-7 · size-8 ·
  size-9`. `h-6` is reserved for micro-chips inside dense rows; `h-10+`
  for hero search fields only. If your control isn't on the ladder, use
  the Button primitive instead of styling a new one.
- **Menus & popovers**: `w-44` (compact action menu) · `w-56` (standard
  menu) · `w-64` (menu with descriptions) · `w-80` (list panel — history,
  notifications). Don't invent in-between widths.
- **Dialogs**: pass `size` to `<DialogContent>` — `sm · md · lg · xl ·
  2xl · 3xl` for forms; `4xl · 5xl` exist for a media player, which is
  not a form. **Never write `max-w-*` in `className`.** The primitive's
  base carries `sm:max-w-sm`, and tailwind-merge cannot replace a
  `sm:`-prefixed class with an unprefixed one — both survive, so the
  prefixed one wins from 640px up and the dialog renders at 384px on
  every desktop. This paragraph used to instruct exactly that, and 31
  dialogs had been shipping at 384px for months before anyone noticed.
  A guard now fails the build for it, so following the old advice broke
  the build rather than the layout. Legacy `w-[480px]`-style dialogs
  migrate to the nearest step as you touch them.
- **Right-docked form drawers** (`<SheetContent side="right">`): pass
  `size` — `sm` (the default, and what every sheet rendered before the
  prop existed) · `md · lg · xl · 2xl`. Write no width class of your own:
  the base is `w-3/4` below the `sm` breakpoint and `sm:max-w-*` above
  it, so a phone gets three-quarters of the viewport and a desktop gets
  the step you named. (This paragraph previously called `w-full`
  load-bearing and promised full-screen on mobile. `sheet.tsx` contains
  no `w-full` at all, and none of its fourteen call sites writes a width
  — the rule described a mechanism that was never there.)
  (The assistant panel is chrome, not a form drawer — it has its own
  resizable width.)

### Layering — the z-index ladder

One ladder, low to high. A new `z-` value outside it is a stacking bug
waiting to happen (tooltip under modal, dropdown under panel):

| Layer | Value | What lives here |
|---|---|---|
| Content | `z-0 · z-10 · z-20` | within-page stacking (pinned cells, hover chrome) |
| Sticky chrome | `z-30` | sticky table headers, filter bars |
| App panels | `z-40` | slide-overs (assistant panel), drawers, launcher |
| Floating UI | `z-50` | menus, popovers, dialogs, tooltips, toasts |
| Above-dialog | `z-[60]` | command palette, media lightbox — the ONE sanctioned arbitrary |
| App blocker | `z-[100]` | the maintenance overlay only — beats everything |

**Maps are exempt where they must match Leaflet's internal pane values**
(`z-[400]`/`z-[500]`/`z-[650]`/`z-[1000]` etc. inside `live-map`/
`geofences` components) — those numbers are Leaflet's, not ours; keep
them next to a comment saying so.

### Icons

- **Library:** [`lucide-react`](https://lucide.dev) only. Don't add a
  second icon set or use inline `<svg>` / emoji as a UI icon (emoji are
  fine in *data* — POI markers, etc.).
- **Size = a standard step.** Pick from **`12 · 14 · 16 · 18 · 20 · 24`**
  (px), matched to the adjacent text:

| Icon | Pairs with | Use |
|---|---|---|
| `12` | `text-2xs`/`xs` | dense inline (table cells, chips) |
| `14` | `text-sm` | default inline (buttons, list rows) |
| `16` | `text-sm`/`base` | standalone / toolbar |
| `18`–`20` | `text-lg`/`xl` | section headers, emphasis |
| `24` | hero | empty-states, big callouts |

- **Colour** comes from a token — `text-muted-foreground`, `toneText('warn')`,
  `text-primary` — never a raw palette class.
- **Convention: the CLASS, never the prop.** `<Plus className="size-4" />`,
  not `<Plus size={16} />`. The two used to be interchangeable and are
  not any more: lucide's `size` prop writes `width`/`height` ATTRIBUTES
  on the `<svg>`, which no multiplier can reach — measured, a
  `size={16}` icon is 16px at 1× and still 16px at 1.5×, while every box
  and word around it grows. The class rides `--size-control` and comes
  out at 24px. The ladder in class form:

  | px | 10 | 12 | 14 | 16 | 18 | 20 | 24 |
  |---|---|---|---|---|---|---|---|
  | class | `size-2.5` | `size-3` | `size-3.5` | `size-4` | `size-4.5` | `size-5` | `size-6` |

  `size-4.5` is declared in `tailwind.config.js` because 18 is a
  sanctioned icon step that Tailwind's spacing ladder skips (it jumps
  16 → 20). It is added to the `size` key only — never to `spacing`,
  which would fuse the four axes.

  **Inside a `<Button>`, write no size at all.** The button's variant
  already sets its icon size (`[&_svg:not([class*='size-'])]:size-3.5`
  on `sm`, and so on), and `tailwind-merge` makes the variant win over
  the base — so an explicit class there opts the icon OUT of the
  button's own vocabulary. Measured: an icon in `<Button size="sm">`
  renders 14px whatever the prop said.

  A wrapper that takes a numeric `size` from ITS callers (`InfoTip`,
  `PoiIcon`, `EventIcon`) cannot know the number until runtime — those
  translate through `iconSizeClass()` in
  [`src/lib/iconSize.ts`](src/lib/iconSize.ts), the single place that
  mapping lives. **Don't** invent in-between sizes (`size-[11px]`,
  `size={13}`).

---

## 8. Charts & maps

### The ramp rotates with the accent

Slot 1 **is** the accent — pick green in the picker and `--chart-1` is
green, in both modes, and guard 34 fails if an accent block re-points
`--primary` and forgets it.

That creates a problem the base ramp does not have. The five base hues
are 264 / 142 / 95 / 30 / 200, and slot 1 is already one of them, so an
accent that matches a LATER slot duplicates it. Green did: slot 1 and
slot 2 both landed on hue 142 — dE2000 **6.70** on dark and **7.79** on
light — where every other pair in
every cell is ≥ 26.9. Two chart lines the same colour — and it is not
only charts, because 41 `*-chart-N` classes across 7 feature files use
the ramp as a categorical BADGE palette, two of them putting the
colliding slots side by side (`settings/WorkHours`' owner and admin rows,
`scorecards/DriverInsights`' compliance and efficiency).

The rule: **slot 1 takes the accent; whichever slot that collides with
moves to a hue clear of every remaining slot.**

It used to read "…is replaced by the hue slot 1 displaced", and that was
green's answer mistaken for the rule. Blue happens to sit far from
everything green leaves behind, so putting it back worked — and it does
not generalise. Azure collides with slot 5 rather than slot 2, and the
displaced blue is only 50 degrees away from it: dE2000 **18.8**, still
under the floor. The displaced hue is the first candidate to try, never
the answer.

| accent | slot that moves | to | why |
|---|---|---|---|
| blue | — | — | slot 1 was already blue, nothing moved |
| purple | — | — | purple displaced blue, and nothing else is near purple |
| green | slot 2 | **blue (264)** | green repeated slot 2's 142; blue comes back and clears |
| azure | slot 5 | **magenta (316)** | azure's 214 is 13° off slot 5's 201; blue reaches only 18.8, 316 reaches 32.8 light / 36.9 dark |

Chroma is the SLOT's, clipped to what sRGB holds at the slot's lightness
for the new hue — never the accent's. Light keeps 0.15 outright (the
ceiling there is 0.2484, so it never binds); dark's 0.18 does not fit
under 0.1449 and comes down to 0.142. Worst pair across all six cells is
now 26.89.

Never write `var(--chart-N)` in a component. It pins that component to a
slot NUMBER, which the rotation makes a moving target, and it bypasses
`chartColor()` — the only place that knows the ramp wraps (`chartColor(0)`
deliberately returns slot 5). Guard 37 fails on it.



**Vendor chrome follows the axis; three vendor circles do not.** Leaflet's
popup, tooltip and zoom bar and sonner's toast, action button and loading
bar are themed from `index.css` with one extra ancestor class each — never
`!important`, and never at equal specificity, because both vendors inject
their stylesheets at runtime and the winner would otherwise depend on
build mode (sonner is imported before `index.css`, so an equal-specificity
rule wins under `vite dev` and loses in the production build — it would
look fixed and ship broken). Left alone on purpose: sonner's close button
and MarkerCluster's two nested discs are CIRCLES, and
`.leaflet-control-layers` is dead — this app never constructs a layers
control.

**One boundary, one object.** Where two surfaces share an edge, exactly
one of them describes it. A straight rule that runs past a rounded
neighbour leaves an `r × r` quarter-disc of whatever is behind it — 4px
at Sharp, 20px at Pill — and a square child inside an unclipped rounded
parent paints `0.293r` over its own arc. Both artifacts GROW as the
reader asks for softer corners, which is why a sweep found ten of them
the day Pill shipped and none of them had ever been reported.

The recurring causes, so they are recognisable: a header drawing a
`border-b` for a card below it (the card's own edge should); an inner
radius larger than its parent's (`rounded-xl` is `--radius + 4`, so it
NEVER fits inside a `rounded-lg` box at any preset); a header docked on
three edges keeping four rounded corners; and a container that grants a
radius but never a clip.

That last one has a mechanism, so name it: **a rounded box whose child
bleeds to its edge needs `overflow-hidden`.** `<Card padding="none">`
carries it — that variant exists precisely for children that own the
card's edges, which is precisely the case that must clip, and leaving it
to the call sites meant four of twenty-two forgot. A call site that
genuinely must not clip says `overflow-visible` and wins, because
`className` merges last. The real case for opting out is a `sticky`
child bound to an OUTER scroller: clipping makes the parent a scroll
container and the pin dies, silently.

**Sharp is `0px`, and the frame keeps a hairline.** The preset means what
its name says: `sm`, `rounded`, `md` and `lg` all render a true right
angle, which is 1098 of the 1120 sites that track the token. Twenty-two
keep a curve, and that is a decision rather than a leftover — `xl` (4px),
`2xl` (8px) and `3xl` (16px) survive because they sit on the surfaces that
FRAME or FLOAT ABOVE everything else: the shell's `<main>`, the Dialog
primitive, the avatar and theme menus, the assistant panel, the two map
control panels. Sharp squares the content; the frame keeps a hairline, so
a modal still reads as sitting on top of the page rather than cut out of
it.

**Write it `0px`, never `0`.** The scale is `calc(var(--radius) + 4px)`,
and `calc(<number> + <length>)` is invalid — a bare `0` collapses all
seven steps instead of four. Worse, `lib/radius.ts` and `DataGrid.tsx`
both branch on `.endsWith('rem')` / `.endsWith('px')` and fall through to
a 10px default, which would put 10px chart-bar corners against 0px CSS.
Measured in Chrome, not reasoned: `0px` gives `xl` = 4px, bare `0` gives
0px. Guarded — every preset's value must carry a unit.

**`--radius` rides NO size axis, and that is deliberate.** It is the
second of exactly two exceptions in this design system — the first is the
24px tap floor, which is a WCAG minimum in CSS pixels and cannot shrink.
This one is different: a corner is a length, and every other length in
the app is multiplied by an axis. This one cannot be, because Tailwind
emits ONE `borderRadius` scale and that scale serves buttons, cards,
panels and dialogs at once. Any axis chosen would be right for one of
them and wrong for the other three.

The cost is real and worth stating, because it is not obvious. The
reachable compound multiplier is `global x region`, and both clamp to
[0.85, 1.5] — so **0.7225 to 2.25, a 3.11x span**. Against the default
Button (`h-8`, 32px at 100%):

| preset | m = 0.7225 | m = 1.0 | m = 2.25 |
|---|---:|---:|---:|
| Sharp (4px) | 17.3% | 12.5% | 5.6% |
| Rounded (10px) | **43.3%** | 31.2% | 13.9% |
| Pill (16px) | 69.2% (stadium) | 50.0% (stadium) | **22.2%** |

Read the two bold cells together: a reader on the smallest Size with
**Rounded** sees proportionally rounder buttons than a reader on the
largest Size with **Pill**. The preset ordering inverts between two users
of the same build, and Pill stops being a literal stadium above m = 1.0.

That is the price of one scale serving four axes, and it is the cheaper
price. Coupling would trade a cross-user inconsistency for a
within-screen one, where a card and a button on the same page disagree
about how round the theme is. Guarded: the `borderRadius` block may
contain no `var(--size-*)`.

**Chart GEOMETRY follows the Corners setting; map geometry does not.**
A chart bar is a rectangle on a page like any other and rounds with it —
but through `components/charts/RoundedBar.tsx`, never the `radius` prop.
Two reasons, both mechanical: recharts takes a NUMBER (`getRectanglePath`
branches on `radius === +radius`, so `'var(--radius)'` draws square
corners and reports nothing), and `radius` is one static tuple for a
whole series, so it cannot know how thick any individual bar turned out.
A 6px bar with a 16px corner is a lozenge, and a bar's height IS its
value — so the corner is **clamped to half the bar's smaller dimension**.
Map markers keep the exemption below for the same reason their lengths
do: they are measured against tiles with their own zoom.

**Map-canvas LENGTHS ride the map's own scale, not the Size engine.**
A marker, its badge and its label are measured against the tiles they sit
on, which have their own zoom; growing them with the interface setting
would put two scales in one viewport and make a zoomed-out map unreadable
at 130%. So the pixel values inside a Leaflet `divIcon` — `usePoiLayers`,
`config/poiLayers.ts`, `live-map/sections/*`, and the two map control
panels — are a sanctioned exception, named in `chrome.test.ts` rather
than left to judgement. Chart text is NOT covered by this: it is read
like any other text and rides `--size-text` through `lib/chartText.ts`.

**Scope: any colour consumer CSS cannot reach** — charts, map markers,
canvas, and 3D materials. All of them take tokens through a helper or a
config constant, never an inline literal in a component.

Six files are allowed literals, and `COLOUR_LITERAL_ALLOWED` in
`components/ui/chrome.test.ts` is the enumerated list — each entry
carries its own reason, and guard 35 fails on a literal anywhere else:

| file | why |
|---|---|
| `config/mapColors.ts`, `config/poiLayers.ts` | painted over tile imagery; a themed marker disappears against half the world |
| `config/documentColors.ts` | signature ink — a PNG filed against an FMCSA application outlives the session that drew it |
| `features/truck-anatomy/colors.ts` | reads the tokens off the element for WebGL; these are the fallbacks for the frame before they are there |
| `features/truck-anatomy/AssemblyNode.tsx` | `new THREE.Color('#000000')`, a "no emissive" lerp target — not a colour anyone sees |
| `features/applications/public/theme.ts` | computes a readable label for a colour the CUSTOMER chose at runtime |

Two shapes are data rather than chrome and are excluded by shape, not by
file: a `<input type="color">` default, and a `placeholder="#2563EB"`
showing the user the format.
These need literal colour strings (Recharts `fill`, Leaflet markers) —
they can't take Tailwind classes. **Still don't hardcode hex:**

- **Charts:** use `chartColor(n)` from `lib/status.ts`, which returns the
  `--chart-1..5` tokens. For status-coloured bars use `var(--ok)` etc.
- **Maps:** marker/route colours belong in a shared palette constant
  (e.g. [`config/poiLayers.ts`](src/config/poiLayers.ts)), referenced —
  not re-typed per layer. Keep all map hex in config, never inline in a
  `<...Layer>` component.

---

## 9. The other two apps

| App | Language | Why separate |
|---|---|---|
| `dashboard` | This doc — shadcn + oklch tokens + theme picker | Customer-facing, themeable |
| `system_dashboard` | Slate-scale console, single accent, no theme picker | Internal tool for ~3 operators; deliberately reads as a "console" |
| `miniapp` | Telegram-native (bridges `tg.themeParams`) | Must match the host Telegram theme |

They are intentionally distinct and do **not** share this token set.
This doc governs `dashboard` only.

---

## 10. Performance budgets

Speed limits for user-facing surfaces — adopted 2026-08-21 from the
instrumented Dispatch-KPI audits, owner-approved.  A change that
breaks one of these does not ship; an audit argues against these
numbers, never from scratch.

| SLI | Budget | Note |
|---|---|---|
| INP, any gesture | **< 200 ms @ 4× CPU throttle** | the human "frozen" line, on a cheap-laptop simulation |
| Gesture settle (click → last work) | **< 300 ms @ 1×, < 800 ms @ 4×** | heavy boards assemble AFTER the click — INP alone under-describes them |
| Long tasks during a gesture | **none > 50 ms** | input-blocking |
| CLS on load | **≤ 0.1** | reserve space for late data — nothing may shove the page |
| DOM nodes, board-class views | **≤ 2,500 at any fleet size** | volume is the multiplier behind every other cost |
| Time to primary data on screen | **≤ 2,000 ms** | |
| Server share of the load path | tracked, **≤ 35 %** | frontend work cannot fix a backend-bound load |
| Frame budget | `1000 / refresh-rate` ms | 10 ms on a 100 Hz display — never assume 16.7 |

**How to measure — the METHOD lives in exactly one place:** the
`ux-audit-performance-interaction` skill
([.claude/skills/ux-audit-performance-interaction/SKILL.md](../../.claude/skills/ux-audit-performance-interaction/SKILL.md))
owns the whole protocol — the DevTools passes, ×3-with-spread, the
4×-throttle doctrine, the seeded-rig pattern, never-a-real-account,
the disposable-measurement and workspace-isolation rules, and the
bundled universal runner (`tools/measure.mjs`).  This section holds
only THIS project's numbers — the values layer the skill reads in its
Step 0b.  This project's kept harness:
[abc-lab/skills/ux-audit-performance-interaction/perf-rig/](../../abc-lab/skills/ux-audit-performance-interaction/perf-rig/README.md) — the seeded
12-dispatcher / 69-truck board for A/B runs.

## 11. Hard rules (the enforcement checklist)

- ❌ No `#hex` in `.tsx` components (charts/maps → config or `chartColor()`).
- ❌ No raw Tailwind palette for meaning: `text-red-500`, `bg-green-100`,
  `border-amber-500` → use `toneClasses()` / status tokens.
- ❌ No arbitrary spacing/size (`p-[13px]`, `h-[42px]`) for layout.
- ❌ No arbitrary text size (`text-[10px]`, `text-[11px]`) → use the scale,
  incl. `text-2xs` / `text-3xs`.
- ❌ No off-step icon sizes (`size={11}`, `size={13}`, `size={22}`) → use
  `12 · 14 · 16 · 18 · 20 · 24`; lucide-react only.
- ❌ No improvised heading styles — use the §4 **role** combos (page title
  `text-2xl font-bold`, section title `text-lg font-semibold`, card title
  `text-base font-semibold`). A heading is the same size+weight everywhere.
- ❌ No `font-mono` on human-readable data (company codes, names, statuses)
  — mono is for machine identifiers (IDs/IPs/hashes/code) only.
- ❌ No hardcoded radius (`rounded-[10px]`, `rounded-4xl`).
- ❌ No re-implemented primitives — use `ui/*` and `shell/*`.
- ❌ No off-ladder control heights — interactive controls sit on
  `h-7 · h-8 · h-9` (`size-7/8/9` for icon-buttons); menus/popovers on
  `w-44 · w-56 · w-64 · w-80`; new dialogs on `size="lg|xl|2xl"` (§7).
- ❌ No z-index outside the §7 ladder (`0–20` content · `30` sticky ·
  `40` panels · `50` floating UI · `z-[60]` above-dialog · `z-[100]`
  maintenance blocker). Map components matching Leaflet pane values are
  the documented exception — comment them.
- ❌ No **inherited** text colour on a surface — declare `text-foreground`
  / the matching `*-foreground`. `bg-<x>` and `text-<x>-foreground` travel
  together (see §2 "Declare colour"). A control with a `bg`/`placeholder`
  but no value colour is the classic light-theme-invisible bug.
- ❌ No raw palette in **`index.html`** either (it's in scope) — and there is
  now **no allowed exception**: the always-dark splash `bg` that used to be
  one is gone, replaced by the pre-paint theme stamp (§2). Body carries only
  `text-foreground`. A raw near-white default there is what made input text
  invisible on light; the splash literal is what left light users on a dark
  canvas.
- ✅ Colour comes from a token; status from a tone; spacing from the 4px
  scale; radius from `--radius`; type from the Geist scale (2xs is the
  smallest; `3xs` is retired and aliased)
  at a §4 role combo; icons from lucide at a standard step.
- ✅ Verified in **both** themes — light and dark — before shipping a new
  surface. Low contrast in one theme is a bug even if the other looks fine.

When unsure, grep for an existing screen that does the same thing and
copy its tokens.

### Which of these are GUARDED

A rule that lives only in this file decays — twice in one session an
audit found violations of rules written here for months, and a later
sweep found this very table three years' worth of guards out of date,
listing ten of the fifteen that existed. Keep it current: a row missing
from here reads as "not enforced", which is how a rule gets broken on
purpose.

These fail `npm test`. Thirty-nine live in `src/components/ui/chrome.test.ts`;
the rest are noted per row. That count is itself checked — add a guard
there and this sentence has to move with it, which is the only reason
this table has any chance of staying true.

| Rule | Guard |
|---|---|
| 24px pointer floor, HEIGHT (§5.1) | computes each control's height from its classes; **validated against 728 elements measured in Chrome — 428/428 verdicts matched**, and it abstains on the 116 whose line-height is inherited |
| 24px pointer floor, WIDTH (§5.1) | icon-only controls only — a control with text is as wide as its text, so it abstains on those (164 of 195). **Validated the same way: 31 controls rendered in Chrome, predicted width matched `getBoundingClientRect()` 31/31.** Added after thirteen targets turned up 14–22px wide while passing the height floor |
| never extend `spacing` (§5.1) | reads `tailwind.config.js` |
| no `calc(100vh − Nrem)` (§5.1) | code lines only, comments excluded |
| no arbitrary px/rem length (§5.1, §11) | viewport units and `var()` deliberately allowed |
| no raw palette for meaning (§11) | prose mentions excluded |
| no emoji as icons (§11) | explicit dingbat list — an arrow in "A → B" is typography, not an icon |
| no native `title=` (§11) | `<iframe title>` exempt: there it is the required accessible name |
| per-user state via the registry | allowlist mirrors `preferences/CLAUDE.md`'s exception table |
| don't hand-roll a Button variant (§7) | a raw `<button>` wearing a variant's own dimensions |
| don't override a Button variant (§7) | dimensions only — a call site may set colour |
| dialog width via `size`, never `max-w-*` (§7) | the base carries `sm:max-w-sm`, which an unprefixed class cannot replace |
| don't restate what a primitive already ships (§7) | the call site repeating a base the component already applies |
| don't hand-roll a Badge (§11) | a `<span>` wearing a status colour plus badge geometry — both the `toneClasses` and `statusClasses` doors |
| chart text rides the text axis (§5.1) | recharts takes `fontSize` as a number and writes an SVG attribute no class reaches; `lib/chartText.ts` holds the replacements |
| every exemption list stays honest | each of the twelve debt lists is paired with the predicate it exempts from, so an entry that no longer offends is reported by name |
| modals are never hand-rolled | `scrolling/backdrops.test.ts` |
| the sheet ✕ is suppressed where the header has one | `components/ui/sheetClose.test.ts` |
| a locale never falls further behind English | `locales/parity.test.ts` — per-locale ceilings, plus: a translation may not carry a key English does not have |
| don't hand-roll the card surface (§6) | narrow on purpose: `rounded-md` is the chip radius, `bg-card/NN` is translucent floating chrome, and `absolute/fixed/sticky` is a thing that floats — the first draft matched 50 sites and most were not cards |
| no literal length in an inline `style` (§5.1) | the one spelling the arbitrary-length rule cannot see — a number in a style object is not a class. `lib/scaledLength.ts` is the replacement, mirroring the config's axis-by-magnitude rule |
| the tap floor rides no axis (§5.1) | reads `tailwind.config.js` — `tap` must stay a literal `24px`, because `tapHeight`/`tapWidth` return a hardcoded 24 for it and would keep saying so if it ever became scalable |
| no `text-3xs` (§7) | the 10px step is retired — it rendered 8.5px at the 85% floor. Aliased to `2xs` in the config so any straggler still renders, but nothing new may use it |
| no corner outside the scale (§6) | `rounded-[Npx]` ignores the setting; `rounded-4xl` is worse — a step the scale never defined, so it compiles to nothing and falls back to square. Comments are stripped first, so StatusBadge's write-up of the old bug is not itself a bug |
| a capsule is never a control (§3) | `rounded-full` on a text-bearing `<button>`/`<a>`. Circular BY GEOMETRY — an icon button in a square box — is exempt and detected by its own size classes |
| raw form controls stay above the tap floor (§5.1) | the tap-floor guard scans `<button\|a\|summary>` only, so every raw `<input>`, `<textarea>` and `<select>` was invisible to it — which is how `min-h-tap` went missing from the `<Input>` primitive and all 66 of its call sites. A two-way ratchet: 243 raw controls predate it, so it fails when the number RISES (a new offender) and when it FALLS (debt paid, baseline now a lie) |
| the three Corners presets stay wired | reads `index.css` — the first test in this repo to open a stylesheet. Without it a fourth preset ships as a silent no-op, and an existing one can be neutered by deleting a line |
| `--radius` is assigned only in the stylesheet | a component redefining it would scope the picker to its own subtree, and the half-failure is invisible until someone tries Sharp |
| the borderRadius scale still derives from `--radius` | reads `tailwind.config.js`; `none` and `full` are deliberately literal |
| a primitive never animates its own geometry (§7) | `transition-all` on Button or Badge animates height, padding and radius with the colours — and the Size slider paints every drag frame, so each one retargeted an in-flight 150ms transition. `transition-control` names what a control may animate |
| a rounded popup clips its rows (§6) | a square child painted over its parent's arc is 0.293r — 4.7px at Pill — and it GROWS as the reader asks for softer corners |
| no viewport-wide fixed strip outside the shell (§6) | the shell owns the frame; a `fixed bottom-0 left-0 right-0` bar reaches into the sidebar and swallows both of the card's bottom corners |
| the edge-to-edge card variant clips (§6) | `padding="none"` is for children that own the card's edges, which is exactly the case that must clip. Leaving it to 22 call sites meant four did not |
| a child is never rounder than the parent it sits against (§6) | the steps are fixed OFFSETS, so an `xl` is rounder than an `lg` at every preset — no measurement needed. Adjacency is approximated by source proximity (8 lines); loosen it and the rule dissolves — 0 hits at 8, 9 at 20, 41 at no limit, the furthest pair 325 lines apart with arcs that never meet |
| print puts the light palette back (§6) | `@media print` re-declares, at its `:root` value, every colour token the dark themes override, and the guard asserts that set equality both ways plus byte-equal values. Without the reset a dark-mode user prints white-on-a-background-the-printer-drops — a blank page. Nobody reviews a printout, so the drift would live for years |
| every class the code writes actually compiles (§6) | compiles every class the codebase writes — variant-prefixed AND bare — against the real config, and fails on any that emits no rule. Three structural filters keep it allowlist-free (comments stripped, so an apostrophe in prose cannot open a fake string; a literal followed by `:` is an object key, not a class; index.css's own classes count as emitted). Found 36 dead classes: nine primitives' v4 variants, plus `backdrop-blur-xs` on every modal scrim, `max-w-55` beside a `truncate`, `field-sizing-content`, `outline-hidden`, `underline-offset-3` and `[a]:hover:` on every Badge rendered as a link. Reading the files cannot find any of it |
| every accent the picker offers has CSS, in both modes (§2) | reads `THEME_ACCENTS` out of the registry and requires a `--primary`, a `--chart-1`, a `--primary-hover` AND a `--primary-text` under both `:root:not(.dark)[data-accent=…]` and `.dark[data-accent=…]`, plus a swatch in each mode. An accent needs a value per mode — a `--primary` bright enough for near-black is too pale to clear AA on white — so shipping only the dark half makes the chip a no-op in Light, silently |
| every rendered colour pair meets its WCAG floor (§2) | `src/lib/colour.test.ts` — resolves every token through the six mode×accent cells, composites alpha fills over each ground the way a browser does (a tone pill on a card is not the same colour as the same pill on the canvas), and applies 4.5 for text / 3.0 for non-text UI. It cannot land green: the failures it still carries are listed WITH the reason each one stays, and the guard fails if a listed pair starts passing, so the list is a work queue rather than an allowlist |
| no colour token drifts further outside sRGB (§2) | same file — an out-of-gamut oklch is not the colour on screen, so any ratio computed on the authored value is arithmetic about a colour nobody has seen, and nudging the chroma of a clipped token moves nothing except on a P3 display. A ratchet, keyed per THEME CELL rather than per token: eight tokens clip today, and taking the worst across cells would let a regression in one hide behind a bigger number in another. A new offender, or an existing one clipping harder, fails |
| no colour literal outside the files allowed one (§8) | a hex in a component cannot follow the theme, cannot be retuned, and is invisible to the contrast guard, which measures TOKENS — `#666` inside a Leaflet popup string sat at 2.21:1 on the dark themes for as long as it existed. The allowlist is by FILE because the legitimate reasons cluster (painted over tile imagery; a signature PNG that outlives the session; a WebGL fallback for the frame before the tokens land), and each entry states its reason. A colour INPUT's default and a `placeholder="#2563EB"` are excluded by SHAPE, not by file — a file entry would have excused everything else in those files, the mistake `INLINE_LENGTH_ALLOWED` already recorded |
| the map SSOT keeps its popup section token-only (§8) | the allowlist is per file, so an allowed file can grow a literal in the part that should be tokens — which mapColors.ts did: four literals under a comment explaining that popups could not use tokens. They can; popup markup becomes real DOM under a `var(--popover)` wrapper, and two of the four were failing AA |
| nothing reads the chart ramp except the tone layer (§8) | `var(--chart-N)` in a component pins it to a slot NUMBER, and the ramp rotates with the accent — slot 2 is green under most accents and blue under a green one, so a raw var silently opts that component out. It also bypasses `chartColor()`, which is the only place that knows the ramp wraps. Nothing else greps for this |
| `codeOnly` blanks exactly the comments (§11) | it is the floor every source-reading guard stands on, and it was wrong three ways at once, each of which made guards pass silently over code: `accept="image/…"` opened what a regex reads as a block comment and swallowed 714 lines of the public applicant form; an apostrophe in JSX text opened a string that ran to the next apostrophe in the file; and a template literal was treated as opaque although `${…}` holds CODE, so the first backtick in a comment written there read as the template's close. A fixture now pins all three, plus that line numbers survive — anything reporting a location depends on that |
| an element-scoped tint sets every accent-derived token (§2) | got wrong three times running — `--ring`, then `--primary-hover`, then `--primary-text` — because the failure is invisible: a custom property's `var()` resolves on the element that DECLARES it, so `:root`'s derivation cannot see a tint applied to a div, and the public apply form keeps the 4truck accent on a carrier-branded page while looking deliberate. The required set is read out of index.css, not listed in the test, because a hand-written list is what goes stale on the fourth token |
| this table lists every guard | counts the guards in `chrome.test.ts` against the number spelled out above it — the table had gone five guards stale before anyone checked |

Three carry NAMED DEBT lists for migrations older than the guards
(`title=`, arbitrary lengths, one Button override). A separate test
fails if an entry on any list stops offending, so a list cannot outlive
its reason.

**Not guarded, and worth knowing:** heading ROLE combos (§4), the
`font-mono` rule, icon step values, and whether two sibling surfaces
agree on a legal value. The last is not a guard's job at all — a guard
checks that a value is legal; only a primitive can make siblings
agree.
