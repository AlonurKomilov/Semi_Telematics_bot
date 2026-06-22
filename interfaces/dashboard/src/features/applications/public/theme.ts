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
  } as import('react').CSSProperties;
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
