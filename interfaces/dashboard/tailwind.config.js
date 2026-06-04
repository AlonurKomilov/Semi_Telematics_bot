/** @type {import('tailwindcss').Config} */

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
        destructive: { DEFAULT: tokenColor('--destructive') },
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
      // Corners preset in the theme picker (sharp / default / pill /
      // rounded) reshapes the whole UI in one keystroke.  Before this
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
      borderRadius: {
        DEFAULT: 'max(0px, calc(var(--radius) - 3px))',
        sm: 'max(0px, calc(var(--radius) - 4px))',
        md: 'max(0px, calc(var(--radius) - 2px))',
        lg: 'var(--radius)',
        xl: 'calc(var(--radius) + 4px)',
        '2xl': 'calc(var(--radius) + 8px)',
        '3xl': 'calc(var(--radius) + 16px)',
      },
      // Dense-data micro sizes BELOW Tailwind's `text-xs` (12px) floor.
      // This UI needs sub-12px label text (table meta, chips, axis hints)
      // and ~226 spots were hardcoding it as `text-[10px]` / `text-[11px]`
      // / `text-[9px]` — the same value re-typed inconsistently.  These two
      // named steps absorb all of them.  Defined size-only (no forced
      // line-height) so they're a 1:1 swap for the arbitrary values they
      // replace — no vertical-rhythm shift.  See design.md §4.
      fontSize: {
        '2xs': '0.6875rem', // 11px
        '3xs': '0.625rem',  // 10px (also absorbs the rare 8–9px)
      },
    },
  },
  plugins: [],
};
