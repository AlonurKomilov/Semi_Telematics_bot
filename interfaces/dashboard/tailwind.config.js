/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#eef7ff', 100: '#d9edff', 200: '#bce0ff', 300: '#8eccff', 400: '#59b0ff', 500: '#338cff', 600: '#1a6bf5', 700: '#1355e1', 800: '#1646b6', 900: '#183d8f' },
        // shadcn CSS-var tokens — lets Tailwind JIT generate bg-primary, text-muted-foreground etc.
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        card: { DEFAULT: 'var(--card)', foreground: 'var(--card-foreground)' },
        popover: { DEFAULT: 'var(--popover)', foreground: 'var(--popover-foreground)' },
        primary: { DEFAULT: 'var(--primary)', foreground: 'var(--primary-foreground)' },
        secondary: { DEFAULT: 'var(--secondary)', foreground: 'var(--secondary-foreground)' },
        muted: { DEFAULT: 'var(--muted)', foreground: 'var(--muted-foreground)' },
        accent: { DEFAULT: 'var(--accent)', foreground: 'var(--accent-foreground)' },
        destructive: { DEFAULT: 'var(--destructive)' },
        // Semantic status hues — the meaning layer (ok/warn/danger/info).
        // Each tone has three tokens: solid (text/icons), `-bg` (15%
        // tinted fill) and `-bd` (30% border).  The alpha is baked into
        // the CSS var (color-mix, see index.css) rather than applied via
        // Tailwind's `/15` modifier — the modifier silently drops on
        // full-oklch() var colours in this setup.  Always reach for these
        // (or toneClasses() in lib/status.ts) instead of raw
        // `text-green-500` / `#22c55e` — see design.md.
        ok: 'var(--ok)',
        'ok-bg': 'var(--ok-bg)',
        'ok-bd': 'var(--ok-bd)',
        warn: 'var(--warn)',
        'warn-bg': 'var(--warn-bg)',
        'warn-bd': 'var(--warn-bd)',
        danger: 'var(--danger)',
        'danger-bg': 'var(--danger-bg)',
        'danger-bd': 'var(--danger-bd)',
        info: 'var(--info)',
        'info-bg': 'var(--info-bg)',
        'info-bd': 'var(--info-bd)',
        border: 'var(--border)',
        input: 'var(--input)',
        ring: 'var(--ring)',
        // Shell-chrome tokens — used by the persistent sidebar and the
        // top header bar.  Distinct from ``card`` (which is the surface
        // colour of standalone content cards) so the chrome reads as
        // chrome and the canvas reads as canvas — Samsara-style depth
        // hierarchy instead of one flat dark tone.
        sidebar: {
          DEFAULT: 'var(--sidebar)',
          foreground: 'var(--sidebar-foreground)',
          accent: 'var(--sidebar-accent)',
          'accent-foreground': 'var(--sidebar-accent-foreground)',
          border: 'var(--sidebar-border)',
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
    },
  },
  plugins: [],
};
