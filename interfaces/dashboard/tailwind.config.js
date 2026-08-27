/** @type {import('tailwindcss').Config} */

// The `.js` extension is required: this file is ESM and Node's resolver
// does not add it for a package subpath.
import defaultTheme from 'tailwindcss/defaultTheme.js';

/**
 * CSS-var token colour that supports Tailwind's `/<alpha>` opacity
 * modifier.
 *
 * Plain `'var(--x)'` colours can't take the modifier in this setup: our
 * tokens are complete `oklch(…)` values (not bare channels), so Tailwind
 * can't inject an alpha and emits NO class at all for `bg-primary/80`,
 * `border-border/50`, `bg-destructive/10`, … — they silently render as
 * nothing.  This function form returns the bare var when there's no
 * modifier (identical to before) and a `color-mix()` when there is, so
 * every token supports `/<alpha>` uniformly.  `color-mix` is supported
 * in all evergreen browsers (this is an internal tool).
 *
 * Tailwind calls colour functions with `{ opacityVariable, opacityValue }`.
 * For a bare utility (no modifier) it passes the `--tw-*-opacity` hook as
 * `opacityValue` (e.g. `'var(--tw-bg-opacity, 1)'`); for `/50` it passes
 * the literal `'0.5'`.  We only want color-mix for the literal case, so a
 * bare `bg-card` stays the simple `var(--card)` it always was — and we
 * don't pay color-mix on the thousands of unmodified token usages.
 */
const tokenColor = (cssVar) => ({ opacityValue }) =>
  (opacityValue === undefined || String(opacityValue).startsWith('var('))
    ? `var(${cssVar})`
    : `color-mix(in oklab, var(${cssVar}) calc(${opacityValue} * 100%), transparent)`;

/* ────────────────────────────────────────────────────────────────────
   THE SIZE ENGINE

   Every length Tailwind emits is multiplied by one of four CSS
   variables, so the user's Size control reshapes the whole app without
   a single class name changing.  `var(--size-x, 1)` means the default
   is "unscaled" — with nothing set, `calc(1rem * 1)` computes to
   exactly what it computed before, so this is a no-op until someone
   drags the slider.

     --size-text      type size and its line box
     --size-control   things a finger or cursor aims at (≤3rem)
     --size-layout    breathing room, and fixed content columns (3–6rem)
     --size-panel     surfaces that hold content (>6rem)

   WHY FOUR AND NOT ONE.  A user asking for "bigger" usually means
   bigger TEXT; a user on a cab tablet wants bigger TARGETS; neither
   wants their menus to grow into the viewport.  Splitting the axes is
   what lets one move without the others.  The UI ships one slider that
   drives all four together — the axes are wired now so exposing them
   individually later is a UI change, not an engine change.

   THE ONE RULE: never extend `spacing`.  It is the shared source every
   dimension key DEFAULTS from, so extending it moves padding, gap,
   margin, width, height and size at once and collapses the four axes
   back into one.  Extend the derived keys instead — verified: extending
   `padding` moves `p-*` alone and leaves `gap-3`/`w-3`/`h-3` untouched.

   WHY GENERATED, NOT HAND-WRITTEN.  These are 17 scales of ~35 steps.
   Hand-maintaining ~600 formulas is how a step silently keeps its old
   literal and stops following the control — so the tables are derived
   from Tailwind's own defaults and the axis rule is stated once, below.
   ──────────────────────────────────────────────────────────────────── */

/** A length in rem, or undefined for anything with no length to scale
 *  (`auto`, `100%`, `min-content`, `1px` hairlines, `0`). */
const remValue = (v) => {
  const m = typeof v === 'string' && /^(\d*\.?\d+)rem$/.exec(v.trim());
  const n = m ? parseFloat(m[1]) : NaN;
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

/**
 * One step, on one axis, times whatever REGION it happens to render in.
 *
 * The region factor is here rather than in a wrapper that recomputes the
 * four axes, because a custom property cannot be defined in terms of its
 * own inherited value — `--size-text: calc(var(--size-region-x) *
 * var(--size-text))` is a cycle and drops to the guaranteed-invalid
 * value. Multiplying at the point of USE has no such problem: a wrapper
 * sets `--size-region` alone and every length below it follows.
 *
 * Unset it costs nothing — `var(--size-region, 1)` is the identity, and
 * the emitted CSS is byte-identical in behaviour to the two-factor form
 * for every element outside a region.
 */
const scaled = (value, axis) =>
  `calc(${value} * var(--size-${axis}, 1) * var(--size-region, 1))`;

/** Breathing room, offsets and gaps — one axis, no size test needed. */
const layoutScale = (source) => Object.fromEntries(
  Object.entries(source)
    .filter(([, v]) => remValue(v) !== undefined)
    .map(([k, v]) => [k, scaled(v, 'layout')]),
);

/**
 * Box dimensions, split BY SIZE because one Tailwind key serves two
 * different design ladders.  `w-8` is an icon button; `w-56` is a menu.
 * Tailwind resolves both from `width`, so the split has to happen
 * per-step — which it can, and that is why this costs no call-site
 * edits.  Boundaries:
 *   ≤3rem   a control or an icon box        -> --size-control
 *   3–6rem  a fixed content column          -> --size-layout
 *   >6rem   a panel, menu, drawer or dialog -> --size-panel
 * The middle band is the genuinely ambiguous one (`w-20` at 80px is
 * neither a button nor a panel); it rides layout because it behaves
 * like structure rather than like a target.
 */
/**
 * Steps the app measures in but Tailwind's default ladder does not name.
 *
 * These are not new opinions — every one is a length already in the
 * codebase, written as `w-[220px]` because there was no `w-55` to write.
 * An arbitrary length is the one value the Size multipliers cannot reach
 * (design.md §5.1), so those sites were a promise that the element would
 * never follow the user's setting. Naming the step is what un-promises
 * it: `dimensionScale` picks the axis by magnitude and the calc comes
 * for free.
 *
 * Added to the DERIVED keys only — never to `spacing` itself, which is
 * the shared source all four axes fan out from and would fuse them.
 *
 * The ladder also simply stopped at 384px while the app has an 680px
 * form column and a 512px popover. Half of these are that gap.
 */
const EXTRA_STEPS = {
  15: '3.75rem',    //  60px  row minimum
  22: '5.5rem',     //  88px  calendar cell
  30: '7.5rem',     // 120px  narrow column
  35: '8.75rem',    // 140px  column
  50: '12.5rem',    // 200px  column
  55: '13.75rem',   // 220px  chart box, topbar search
  65: '16.25rem',   // 260px
  70: '17.5rem',    // 280px
  100: '25rem',     // 400px  map minimum
  104: '26rem',     // 416px  floating panel
  120: '30rem',     // 480px
  128: '32rem',     // 512px  popover maximum
  140: '35rem',     // 560px  form column
  170: '42.5rem',   // 680px  wide form column
};

const dimensionScale = (source) => Object.fromEntries(
  Object.entries(source).flatMap(([k, v]) => {
    const rem = remValue(v);
    if (rem === undefined) return [];
    const axis = rem <= 3 ? 'control' : rem <= 6 ? 'layout' : 'panel';
    return [[k, scaled(v, axis)]];
  }),
);

/** Type. Tailwind's steps are `[size, { lineHeight }]` tuples; both
 *  halves must scale or the line box stops matching the glyphs.  The
 *  house `2xs`/`3xs` steps are deliberately size-only (see the note on
 *  `fontSize` below) and stay that way. */
const textScale = (source) => Object.fromEntries(
  Object.entries(source).flatMap(([k, v]) => {
    if (Array.isArray(v)) {
      const [size, opts] = v;
      if (remValue(size) === undefined) return [];
      const lh = opts && opts.lineHeight;
      return [[k, [scaled(size, 'text'), remValue(lh) !== undefined
        ? { ...opts, lineHeight: scaled(lh, 'text') }
        : opts]]];
    }
    return remValue(v) !== undefined ? [[k, scaled(v, 'text')]] : [];
  }),
);

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#eef7ff', 100: '#d9edff', 200: '#bce0ff', 300: '#8eccff', 400: '#59b0ff', 500: '#338cff', 600: '#1a6bf5', 700: '#1355e1', 800: '#1646b6', 900: '#183d8f' },
        // shadcn CSS-var tokens — wrapped in tokenColor() so `bg-primary`,
        // `text-muted-foreground/60`, `border-border/50`, etc. all work
        // (the bare class AND the `/<alpha>` modifier).
        background: tokenColor('--background'),
        foreground: tokenColor('--foreground'),
        card: { DEFAULT: tokenColor('--card'), foreground: tokenColor('--card-foreground') },
        popover: { DEFAULT: tokenColor('--popover'), foreground: tokenColor('--popover-foreground') },
        primary: { DEFAULT: tokenColor('--primary'), foreground: tokenColor('--primary-foreground') },
        secondary: { DEFAULT: tokenColor('--secondary'), foreground: tokenColor('--secondary-foreground') },
        muted: { DEFAULT: tokenColor('--muted'), foreground: tokenColor('--muted-foreground') },
        accent: { DEFAULT: tokenColor('--accent'), foreground: tokenColor('--accent-foreground') },
        // The CATEGORICAL ramp, as classes. `--chart-1..5` have existed as
        // tokens since the theme work and were reachable only through
        // `chartColor(n)`, which returns a CSS VALUE — fine for a chart
        // fill, useless for a badge that needs bg + text + border. So
        // every surface that had to colour a CATEGORY rather than a
        // STATUS reached for the raw Tailwind palette instead: 8 service
        // types, 6 safety-event types, 3 scorecard categories, 2 media
        // types, 2 plan tiers. Twenty-five call sites picking a hue by
        // hand, none of which could follow the theme picker.
        //
        // Tones stay the answer for good/bad/warn/info. This is for sets
        // whose members are merely DIFFERENT from each other.
        chart: {
          1: tokenColor('--chart-1'), 2: tokenColor('--chart-2'),
          3: tokenColor('--chart-3'), 4: tokenColor('--chart-4'),
          5: tokenColor('--chart-5'),
        },
        // The fill and its on-colour travel together, same as `primary`
        // and `card`.  Without the `foreground` key Tailwind had nothing
        // to look up for `text-destructive-foreground`, so that class
        // emitted NO rule and five destructive buttons inherited their
        // label colour from the body instead of declaring it.
        destructive: {
          DEFAULT: tokenColor('--destructive'),
          foreground: tokenColor('--destructive-foreground'),
        },
        // Semantic status hues — the meaning layer (ok/warn/danger/info).
        // The solid tone supports `/<alpha>` via tokenColor().  Each tone
        // also ships pre-baked `-bg` (15% fill) and `-bd` (30% border)
        // tokens for the canonical soft-pill recipe — these are already
        // translucent CSS vars, so they stay as plain `var()` (no
        // modifier applied to them).  Reach for these (or toneClasses()
        // in lib/status.ts) for any status colour — see design.md §3.
        ok: tokenColor('--ok'),
        'ok-bg': 'var(--ok-bg)',
        'ok-bd': 'var(--ok-bd)',
        warn: tokenColor('--warn'),
        'warn-bg': 'var(--warn-bg)',
        'warn-bd': 'var(--warn-bd)',
        danger: tokenColor('--danger'),
        'danger-bg': 'var(--danger-bg)',
        'danger-bd': 'var(--danger-bd)',
        info: tokenColor('--info'),
        'info-bg': 'var(--info-bg)',
        'info-bd': 'var(--info-bd)',
        border: tokenColor('--border'),
        input: tokenColor('--input'),
        ring: tokenColor('--ring'),
        // Shell-chrome tokens — used by the persistent sidebar and the
        // top header bar.  Distinct from ``card`` (which is the surface
        // colour of standalone content cards) so the chrome reads as
        // chrome and the canvas reads as canvas — Samsara-style depth
        // hierarchy instead of one flat dark tone.
        sidebar: {
          DEFAULT: tokenColor('--sidebar'),
          foreground: tokenColor('--sidebar-foreground'),
          accent: tokenColor('--sidebar-accent'),
          'accent-foreground': tokenColor('--sidebar-accent-foreground'),
          border: tokenColor('--sidebar-border'),
        },
      },
      // Every common rounded-* variant tracks ``--radius`` so the
      // Corners preset in the theme picker (Sharp / Rounded / Pill —
      // three, and "Rounded" is the absence of an override: it falls
      // through to the `:root` value, which is why index.css has no
      // `[data-radius="rounded"]` block) reshapes the whole UI in one
      // keystroke.  Before this
      // extension, ``rounded``, ``rounded-xl`` and ``rounded-2xl`` were
      // hardcoded at Tailwind's defaults (0.25rem, 0.75rem, 1rem) and
      // ignored the user's choice — pill-mode buttons still had square
      // corners next to their pill cards, which looked broken.
      //
      // ``max(0px, …)`` guards against an arithmetic negative when a
      // future Corners preset drops ``--radius`` below 4px.
      // ``rounded-full`` (circles, pills) and ``rounded-none`` (intentional
      // squares) are deliberately left at their Tailwind defaults — they
      // describe a shape, not a degree of softness.
      // The UI primitives in components/ui were vendored from a
      // Tailwind v4 edition of shadcn, where `ring-<number>` is a
      // dynamic utility.  This project is on v3, whose ringWidth scale
      // is 0/1/2/4/8 + DEFAULT — so the primitives' authored
      // `focus-visible:ring-3` compiled to NOTHING and every focus halo
      // was silently missing.  Defining the step revives them.
      ringWidth: {
        3: '3px',
      },
      // Container-relative stat grids. `md:grid-cols-4` asks the
      // VIEWPORT, and the assistant panel narrows the CONTAINER without
      // touching it — so a four-up strip held four columns at ~70px each
      // and a currency value painted outside its own card. `auto-fit`
      // wraps on the space actually available, and the minimum rides the
      // layout axis so the wrap point moves with the content inside it.
      gridTemplateColumns: {
        'fit-36': `repeat(auto-fit, minmax(${scaled('9rem', 'layout')}, 1fr))`,
        'fit-48': `repeat(auto-fit, minmax(${scaled('12rem', 'layout')}, 1fr))`,
      },
      borderRadius: {
        DEFAULT: 'max(0px, calc(var(--radius) - 3px))',
        sm: 'max(0px, calc(var(--radius) - 4px))',
        md: 'max(0px, calc(var(--radius) - 2px))',
        lg: 'var(--radius)',
        xl: 'calc(var(--radius) + 4px)',
        '2xl': 'calc(var(--radius) + 8px)',
        '3xl': 'calc(var(--radius) + 16px)',
      },
      // ── The Size axes ───────────────────────────────────────────────
      // Every scale below is Tailwind's own, re-emitted as
      // `calc(step × var(--size-axis, 1))`.  See the axis rules at the
      // top of this file.  `spacing` is absent on purpose and must stay
      // absent — it is the shared default every dimension key derives
      // from, so touching it fuses the axes.

      // Breathing room and offsets.
      padding: layoutScale(defaultTheme.spacing),
      margin: layoutScale(defaultTheme.spacing),
      gap: layoutScale(defaultTheme.spacing),
      space: layoutScale(defaultTheme.spacing),
      inset: layoutScale(defaultTheme.spacing),
      translate: layoutScale(defaultTheme.spacing),
      scrollPadding: layoutScale(defaultTheme.spacing),
      scrollMargin: layoutScale(defaultTheme.spacing),

      // Box dimensions — split per step between control / layout / panel.
      // `size` needs its own entry even though it looks like w+h: it is a
      // SEPARATE Tailwind key, and leaving it out freezes every `size-N`
      // element while its `w-N h-N` siblings grow — which turns a round
      // icon button into an oval.
      width: dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }),
      // `h-tap` exists for ONE case that `min-h-tap` cannot serve: a table
      // row. A row's height comes from the table layout algorithm, not from
      // min-height — measured, `min-h-tap` on a <tr> leaves a compact row at
      // 20.4px at 0.85x. `height` on a <tr> IS treated as a minimum, so this
      // floors the row while a taller row still grows past it.
      height: { ...dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }), tap: '24px' },
      // `size-4.5` = 18px. It is added HERE and nowhere else on purpose:
      // 18 is a sanctioned ICON step (CLAUDE.md: 12·14·16·18·20·24) but it
      // is not on Tailwind's spacing ladder, which jumps 16 -> 20. Without
      // it the 52 `size={18}` icons have no class to migrate to. Adding a
      // step to a DERIVED key costs nothing; adding one to `spacing` would
      // fuse the four axes, which is why it does not go there.
      // `size-tap` joins `min-h-tap` / `h-tap`: the same literal 24px,
      // for a SQUARE floor. slider.tsx drew its thumb's invisible hit
      // area as `after:size-[24px]` — right reasoning (a floor must not
      // shrink with the control it protects), wrong spelling. §5.1 says
      // an arbitrary value emits no rule at all if the scanner never
      // sees the literal, and it is not greppable when the next person
      // audits the floor.
      size: {
        ...dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }),
        '4.5': scaled('1.125rem', 'control'),
        tap: '24px',
      },
      maxHeight: dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }),

      // `min-h-tap` / `min-w-tap` — the pointer-target floor, and the ONE
      // step in this file that deliberately rides no axis.
      //
      // A floor expressed on the Size ladder is not a floor: it shrinks
      // with everything else and stops protecting exactly when it is
      // needed. Measured — the house `p-1 -m-1` invisible-padding idiom
      // gives a 24px box at 1.0 and only 22px at 0.75, and `min-h-6`
      // compiles to `calc(1.5rem * var(--size-control, 1))`, so neither
      // holds the WCAG 2.5.8 minimum once the user shrinks the UI.
      //
      // 24px is the AA floor from WCAG 2.5.8, in CSS pixels, which is why
      // it is a literal and why design.md §5.1's "no arbitrary lengths"
      // rule names this as its one sanctioned exception. A named step
      // rather than `min-h-[24px]` at 500 call sites: greppable, states
      // its intent, and cannot silently fail to exist the way an
      // arbitrary value does when the scanner never sees it.
      minWidth: { ...dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }), tap: '24px' },
      minHeight: { ...dimensionScale({ ...defaultTheme.spacing, ...EXTRA_STEPS }), tap: '24px' },
      // maxWidth is TWO ladders in one key: the named dialog steps
      // (`max-w-lg`) and the whole spacing scale (`max-w-40`). Tailwind's
      // default is a function that merges `theme('spacing')` in, so
      // stubbing `theme` — as this did at first — silently returns only
      // the 17 named steps and leaves all 35 numeric ones at their
      // literals. That is the exact failure the header warns about, and
      // it was the worst possible one to have: `max-w-40 truncate` is a
      // frozen box around growing text, so raising Size showed the user
      // LESS of a filename, not more.
      // `screen-*` and `prose` fall out via remValue() — not rem lengths.
      maxWidth: dimensionScale({
        ...defaultTheme.spacing,
        ...defaultTheme.maxWidth({ theme: () => ({}), breakpoints: () => ({}) }),
      }),

      // Type. Both halves of each tuple scale, or the line box stops
      // matching the glyphs sitting in it.
      lineHeight: textScale(defaultTheme.lineHeight),
      fontSize: {
        ...textScale(defaultTheme.fontSize),
        // Dense-data micro sizes BELOW Tailwind's `text-xs` (12px) floor.
        // This UI needs sub-12px label text (table meta, chips, axis hints)
        // and ~226 spots were hardcoding it as `text-[10px]` / `text-[11px]`
        // / `text-[9px]` — the same value re-typed inconsistently.  These two
        // named steps absorb all of them.  Defined size-only (no forced
        // line-height) so they're a 1:1 swap for the arbitrary values they
        // replace — no vertical-rhythm shift.  See design.md §4.
        '2xs': scaled('0.6875rem', 'text'), // 11px
        // '3xs' (10px) is RETIRED and aliased to 2xs, not deleted.
        //
        // Retired because at the 85% floor it rendered 8.5px, and 145
        // sites used it — including the fuel and DEF percentages on the
        // live map, which are data rather than decoration. No floor could
        // fix that: flooring the smallest steps at 10px puts '3xs' above
        // 'xs' (10.2px at 85%) and inverts the scale. Removing the step
        // does what a floor could not, and costs one pixel at 100%.
        //
        // Aliased rather than deleted because two AIs and a human share
        // this tree. A file mid-edit elsewhere may still carry
        // `text-3xs`, and a DELETED step emits no font-size at all — the
        // element would inherit its parent's, silently, which is a worse
        // failure than the 8.5px this was meant to cure. The alias makes
        // every straggler correct; the guard in chrome.test.ts stops new
        // ones. Delete this line once that guard has been green across a
        // few weeks of everyone's commits.
        '3xs': scaled('0.6875rem', 'text'),  // = 2xs. Retired; do not use.
      },
    },
  },
  plugins: [],
};
