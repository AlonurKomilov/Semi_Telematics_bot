// SINGLE SOURCE OF TRUTH for the public apply form's theme.
//
// apply.4truck.us mounts the form with NO ThemeProvider (see main.tsx), so
// it stays on the light `:root` tokens.  The recruiter preview renders the
// SAME form inside the (possibly dark) dashboard, so it must reproduce the
// public form's theme rather than inherit the dashboard's.
//
// Both surfaces call THIS function, so the public form's appearance is
// defined in exactly one place.  If the form ever becomes theme-aware
// (e.g. a dark carrier theme, or following the OS preference), change it
// HERE and both apply.4truck.us and the preview follow automatically —
// neither is hardcoded to light at its own call-site.
//
// Returns a restore() that undoes the change — the preview uses it to put
// the recruiter's dashboard theme back when they leave; apply.4truck.us
// ignores it (it owns the whole document).
export function applyPublicFormTheme(root: HTMLElement = document.documentElement): () => void {
  const hadDark = root.classList.contains('dark');
  const prevTheme = root.dataset.theme;

  // The public form's GLOBAL base: light `:root` tokens (no `.dark`).  A
  // per-carrier dark form is applied as a `.dark` class on the form root
  // element (scoped), not here — see brandTintStyle / PublicApply.
  root.classList.remove('dark');
  root.dataset.theme = 'light';

  return () => {
    if (hadDark) root.classList.add('dark');
    if (prevTheme !== undefined) root.dataset.theme = prevTheme;
  };
}

// Black or white text for legibility ON a carrier colour (sRGB luminance),
// so a pale brand colour never yields an unreadable button.
export function readableTextOn(hex: string): string {
  const h = (hex || '').replace('#', '');
  if (h.length < 6) return '#ffffff';
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? '#0a0a0a' : '#ffffff';
}

// Inline style that tints the primary UI (button / stepper / progress /
// links) to the carrier colour with a legible foreground — or undefined for
// a generic (untinted) form.  Overriding `--primary` is safe with the faded
// states (`bg-primary/90`): tokenColor() builds them with color-mix(), which
// accepts any CSS colour for var(--primary).  `--brand` stays exposed for
// the header accents.
export function brandTintStyle(brandColor?: string): import('react').CSSProperties | undefined {
  if (!brandColor) return undefined;
  return {
    ['--brand']: brandColor,
    ['--primary']: brandColor,
    ['--primary-foreground']: readableTextOn(brandColor),
    ['--ring']: brandColor,   // focus rings follow the brand accent
  } as import('react').CSSProperties;
}

// Derive a FULL, readable neutral palette from one "surface" colour (the
// card + page base) — replacing the light/dark toggle.  Text is auto-
// contrasted (readableTextOn); borders / muted fills / inputs are blended
// from the surface toward that text via color-mix (works for any surface,
// light or dark).  Status colours (info/ok/warn/destructive) and the brand
// accent are intentionally NOT touched here.  Empty → the default theme.
export function surfaceThemeStyle(surface?: string): import('react').CSSProperties | undefined {
  if (!surface) return undefined;
  const fg = readableTextOn(surface);
  // pct% of the text colour blended into the surface — higher = more contrast.
  const mix = (pct: number) => `color-mix(in oklab, ${fg} ${pct}%, ${surface})`;
  return {
    ['--background']: surface,
    ['--foreground']: fg,
    ['--card']: surface,
    ['--card-foreground']: fg,
    ['--popover']: surface,
    ['--popover-foreground']: fg,
    ['--muted']: mix(8),
    ['--muted-foreground']: mix(55),
    ['--border']: mix(16),
    ['--input']: mix(16),
    ['--secondary']: mix(10),
    ['--secondary-foreground']: fg,
    ['--accent']: mix(10),
    ['--accent-foreground']: fg,
  } as import('react').CSSProperties;
}

// Is a surface colour a poor base for BOTH black and white text? (mid-tones
// where neither gives strong contrast).  Used to warn the recruiter.
export function surfaceContrastWeak(surface?: string): boolean {
  if (!surface) return false;
  const h = surface.replace('#', '');
  if (h.length < 6) return false;
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.42 && lum < 0.62;   // mid-tone — neither black nor white is crisp
}

// Style for an element whose BACKGROUND is a custom carrier colour (the
// header band, the page bg).  Sets the bg + readable text vars SCOPED to
// that element, so its own text/links stay legible while the rest of the
// form keeps the base theme.  Empty → undefined (base default).
export function onColorStyle(color?: string): import('react').CSSProperties | undefined {
  if (!color) return undefined;
  const fg = readableTextOn(color);
  return {
    backgroundColor: color,
    ['--foreground']: fg,
    ['--muted-foreground']: fg === '#ffffff' ? 'rgba(255,255,255,0.72)' : 'rgba(0,0,0,0.6)',
  } as import('react').CSSProperties;
}
