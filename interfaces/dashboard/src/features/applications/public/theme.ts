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
import {
  parseHex, toHex, readableOn, clampLightness, contrastRatio,
  srgbToOklch, srgbInGamut, over, AA_TEXT, type RGB,
} from '../../../lib/contrast';

/** Every custom property the dashboard's Size engine publishes on <html>.
 *  The applicant's host has none of them. */
const SIZE_VARS = [
  '--size-text', '--size-control', '--size-layout', '--size-panel',
  '--size-region-text', '--size-region-tables', '--size-region-controls',
  '--size-region-overlays', '--size-region-navigation', '--size-region-assistant',
] as const;

export function applyPublicFormTheme(root: HTMLElement = document.documentElement): () => void {
  const hadDark = root.classList.contains('dark');
  const prevTheme = root.dataset.theme;
  const prevAccent = root.dataset.accent;
  const prevRadius = root.dataset.radius;
  const prevSize = SIZE_VARS.map((v) => [v, root.style.getPropertyValue(v)] as const);

  // The public form's GLOBAL base: light `:root` tokens (no `.dark`).  A
  // per-carrier dark form is applied as a `.dark` class on the form root
  // element (scoped), not here — see brandTintStyle / PublicApply.
  root.classList.remove('dark');
  root.dataset.theme = 'light';

  // DELETED for the same reason as data-radius below: the accent is now
  // its own attribute, so leaving the recruiter's would tint an
  // applicant preview with a colour the applicant will never see.
  // `apply.4truck.us` carries no data-accent at all — the boot script
  // skips that host — so deleting reproduces it, where setting 'blue'
  // would reproduce the right colour by the wrong mechanism.
  delete root.dataset.accent;

  // DELETED, not set to a value. `apply.4truck.us` is a host the theme
  // boot script deliberately skips, so an applicant's document carries no
  // `data-radius` and no `--size-*` at all — the tokens sit at their
  // `:root` defaults. Setting 'rounded' here would reproduce the RIGHT
  // corners by luck and the wrong mechanism; deleting reproduces the
  // host.
  //
  // Without this the recruiter's own Corners and Size settings leaked
  // straight into a preview of a page that will never have them: 38
  // `rounded-md` sites rendering at 0px on Sharp or 14px on Pill, where
  // the applicant always sees 8px.
  delete root.dataset.radius;
  for (const v of SIZE_VARS) root.style.removeProperty(v);

  return () => {
    if (hadDark) root.classList.add('dark');
    if (prevTheme !== undefined) root.dataset.theme = prevTheme;
    if (prevAccent !== undefined) root.dataset.accent = prevAccent;
    if (prevRadius !== undefined) root.dataset.radius = prevRadius;
    for (const [v, was] of prevSize) if (was) root.style.setProperty(v, was);
  };
}

// Black or white text for legibility ON a carrier colour, and every value
// derived from it.
//
// This whole block used to be arithmetic of its own: the YIQ brightness
// formula (0.299/0.587/0.114) on gamma-encoded bytes, thresholded at 0.6.
// Both halves are the wrong instrument. YIQ weights green 0.587 where
// luminance weights it 0.7152, and skipping the sRGB decode understates
// bright colours — so sampled across 636,056 surfaces it returned the
// WORSE of black and white on 29.9% of them. On #00ff1b it chose white
// at 1.37:1 where black gives 14.44:1, and `brand_color` is whatever the
// carrier typed into a colour picker.
//
// It now uses `lib/contrast`, which is the same code the build-time
// colour guards run. One implementation, so a value proved legible by
// the guard is legible here too.

/** The better of near-black and white on this ground, by measurement. */
export function readableTextOn(hex: string): string {
  const rgb = parseHex(hex);
  return rgb ? toHex(readableOn(rgb)) : '#ffffff';
}

/** What CSS `color-mix(in oklab, a pct%, b)` computes — in JS, because a
 *  value the browser computes is a value we cannot measure. Every wash
 *  below used to be emitted as a `color-mix()` string and was therefore
 *  never checked; that is how `--muted-foreground` came to fail AA on
 *  84% of surfaces without anything noticing.
 *
 *  Interpolation is cartesian, not polar: lerping hue directly sends a
 *  pair either side of 0 degrees the long way round the wheel. */
function mixOklab(a: RGB, pct: number, b: RGB): RGB {
  const A = srgbToOklch(a), B = srgbToOklch(b);
  const rad = (d: number) => (d * Math.PI) / 180;
  const [ax, ay] = [A.C * Math.cos(rad(A.H)), A.C * Math.sin(rad(A.H))];
  const [bx, by] = [B.C * Math.cos(rad(B.H)), B.C * Math.sin(rad(B.H))];
  const t = pct / 100;
  const x = ax * t + bx * (1 - t), y = ay * t + by * (1 - t);
  return srgbInGamut(A.L * t + B.L * (1 - t), Math.hypot(x, y),
    ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360);
}

/** Our own light theme's hairline: `--border` and `--input` both sit at
 *  1.26:1 against `--background`.
 *
 *  Used as the FLOOR for the same two tokens here, and the choice is
 *  deliberate. WCAG 1.4.11 would ask 3:1 of a form-field edge, and this
 *  form is the one place we could quietly deliver that — but a public
 *  page whose fields are outlined twice as hard as the dashboard's reads
 *  as a different product. Both move together when the boundary
 *  redesign happens (784 `border-border` sites, still unclassified into
 *  decorative dividers and real control edges); until then this matches
 *  what we ship, rather than being invisible on a customer's surface,
 *  which is what `mix(16)` alone was on 86% of them. */
export const OUR_HAIRLINE = 1.26;

// Inline style that tints the primary UI (button / stepper / progress /
// links) to the carrier colour with a legible foreground — or undefined for
// a generic (untinted) form.  Overriding `--primary` is safe with the faded
// states (`bg-primary/90`): tokenColor() builds them with color-mix(), which
// accepts any CSS colour for var(--primary) — but a token DERIVED from
// --primary at :root does not follow a tint applied here, so each one has
// to be set explicitly. See --ring and --primary-hover below.
//
// It used to also set `--brand` "for the header accents" — nothing ever
// read var(--brand), so it was a write with no reader. The header takes
// its accent from --primary like everything else.
export function brandTintStyle(
  brandColor?: string,
  /** The carrier's Surface colour, because `--primary-text` has to be
   *  legible ON it and the brand colour alone cannot say what that is. */
  surface?: string,
): import('react').CSSProperties | undefined {
  const brand = parseHex(brandColor || '');
  if (!brand) return undefined;
  const surf = (surface ? parseHex(surface) : null) ?? ([1, 1, 1] as RGB);
  // The label, picked first, because the hover fill is derived FROM it.
  const label = readableOn(brand);
  // By VALUE. `readableOn` hands back its own constant, so `===` against
  // a local array of the same numbers is always false — which silently
  // made the direction below a no-op the first time this was written.
  const labelIsDark = label[0] < 0.5;
  // Hover moves the fill AWAY from the label, so the label's contrast can
  // only improve when the pointer lands on it.
  //
  // It used to always darken — "12% toward black, because this form is
  // always light". That is the right instinct for a dark fill with a
  // white label, and exactly backwards for a pale one with a dark label:
  // darkening a light fill walks it toward the label. Measured across
  // every colour a carrier can enter, always-darken put 17.2% of them
  // under AA on hover, having passed at rest. Choosing the direction
  // from the label costs nothing and the failure disappears — a dark
  // button that lightens on hover is as ordinary as the reverse.
  const hoverRgb = mixOklab(brand, 88, labelIsDark ? [1, 1, 1] : [0, 0, 0]);
  return {
    ['--primary']: brandColor,
    // Derived here as well as in index.css, and that is not a breach of
    // the rule design.md states — it is the reason for it. A custom
    // property's `var()` is substituted when that property is computed
    // ON THE ELEMENT THAT DECLARES IT: `--ring` on `:root` resolves
    // against `:root`'s `--primary`, and children inherit the resolved
    // value. This tint lands on the form-root <div>, so without its own
    // derivation every field on a fully brand-tinted page drew a
    // 4truck-blue focus ring.
    ['--ring']: brandColor,
    // `hover:bg-primary-hover` compiles to a bare `var(--primary-hover)`,
    // NOT a color-mix off --primary — so without this line a carrier's
    // amber CTA hovered to 4truck blue, at 2.75:1 against its own label.
    // (`hover:bg-primary/90`, which it replaced, composited --primary and
    // followed the tint for free. That is the trap: swapping an alpha
    // fade for a token silently breaks every element-scoped override.)
    //
    // Emitted as a resolved colour, not a `color-mix()` string. The
    // browser computes either one identically — but only one of them can
    // be measured by a test, and the label below is picked against this
    // exact value.
    ['--primary-hover']: toHex(hoverRgb),
    // The accent AS TEXT — a third token that does not follow the two
    // above, and the third time this exact trap has been sprung here: a
    // token derived at `:root` resolves against `:root`'s `--primary`,
    // so an element-scoped tint has to set every one of them itself.
    // `text-primary` is on ten sites of this form, including the active
    // step's number, which sits inside a `border-primary` ring — leave
    // it out and the ring is the carrier's colour and the digit inside
    // it is 4truck blue.
    //
    // 40% brand, 60% toward whatever reads on the surface. Not the brand
    // itself: amber on a white surface is 2.15:1, which is the same
    // one-value-two-jobs overload the app-side split just removed. 40%
    // is what survives the worst pairing measured across seven brand
    // colours and four surfaces (5.59:1); at 50% it drops to 4.05.
    //
    // Clamped as well as blended: 5.59:1 was the worst of seven brand
    // colours against four surfaces, which is a spot check, not a floor.
    // Across every colour a carrier can enter, 40% alone drops under AA.
    ['--primary-text']:
      toHex(clampLightness(mixOklab(brand, 40, readableOn(surf)), surf, AA_TEXT).rgb),
    // Picked against the REST and HOVER fills together. The label does
    // not change on hover but the fill darkens 12% toward black, so a
    // near-black label chosen on the rest colour alone loses contrast
    // exactly when the pointer is on it.
    ['--primary-foreground']: toHex(label),
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
  const s = parseHex(surface);
  // A colour we cannot parse is not a theme. It used to be pasted
  // straight into the custom properties, where the browser dropped each
  // one silently and the form rendered half-themed.
  if (!s) return undefined;
  const fgRgb = readableOn(s);
  const fg = toHex(fgRgb);
  /** pct% of the text colour blended into the surface. */
  const wash = (pct: number) => mixOklab(fgRgb, pct, s);
  /** A wash pulled back until it clears `floor` on the ground it is
   *  actually read against. The wash sets the character, the floor
   *  guarantees the reading. */
  const ink = (pct: number, ground: RGB, floor: number) =>
    toHex(clampLightness(wash(pct), ground, floor).rgb);
  // Round-tripped through hex on purpose: this is both the value we ship
  // AND the ground `--muted-foreground` is clamped against, and clamping
  // against the unrounded float guarantees a contrast the browser never
  // sees. That gap alone left 20% of surfaces just under AA.
  const muted = parseHex(toHex(wash(8)))!;
  return {
    ['--background']: toHex(s),
    ['--foreground']: fg,
    ['--card']: toHex(s),
    ['--card-foreground']: fg,
    ['--popover']: toHex(s),
    ['--popover-foreground']: fg,
    ['--muted']: toHex(muted),
    // Clamped against --muted, not the page: secondary text sits on the
    // muted fill as often as on the surface, and muted is 8% nearer the
    // text colour, so it is the harder of the two grounds. Our own light
    // theme puts this pair at 4.54:1, which is what AA asks for and what
    // this now reproduces on any surface — the bare 55% wash was under
    // AA on 84% of them, across 56 sites of this form.
    ['--muted-foreground']: ink(55, muted, AA_TEXT),
    ['--border']: ink(16, s, OUR_HAIRLINE),
    ['--input']: ink(16, s, OUR_HAIRLINE),
    ['--secondary']: toHex(wash(10)),
    ['--secondary-foreground']: fg,
    ['--accent']: toHex(wash(10)),
    ['--accent-foreground']: fg,
  } as import('react').CSSProperties;
}

// Is a surface colour a poor base for BOTH black and white text? (mid-tones
// where neither gives strong contrast).  Used to warn the recruiter.
export function surfaceContrastWeak(surface?: string): boolean {
  const s = surface ? parseHex(surface) : null;
  if (!s) return false;
  // The question is exactly "can ANY text be read here", so ask it.
  //
  // The predecessor guessed at it with a YIQ band (0.42 to 0.62) and was
  // wrong in both directions: over 32,768 surfaces it warned about
  // 10,880 that were perfectly readable — a third of every colour a
  // recruiter could pick, which is how a warning becomes wallpaper — and
  // stayed silent on 161 that genuinely admit no readable text. It is
  // the 161 that this warning exists for.
  return contrastRatio(readableOn(s), s) < AA_TEXT;
}

/**
 * Can text stay readable over the banner photo with this header colour?
 *
 * The counterpart to `surfaceContrastWeak`, and the more useful of the
 * two. A surface colour is now almost always fine — the derivation
 * clamps everything that can be clamped, and the worst case left is
 * 4.45:1 against a 4.5 standard. The hero cannot be rescued that way:
 * its ground is the scrim over a photograph, so the text has to work
 * over a white frame and a black one at once, and at 0.78 opacity only
 * 60.1% of header colours admit any colour that does.
 *
 * That is the 39.9% a recruiter can actually act on — pick a deeper
 * header colour, or drop the photo — and until now nothing told them.
 */
export function heroContrastWeak(headerColor?: string, hasBanner = true): boolean {
  const h = headerColor ? parseHex(headerColor) : null;
  if (!h || !hasBanner) return false;
  const grounds: RGB[] = ([[1, 1, 1], [0, 0, 0]] as RGB[]).map((img) => over(h, 0.78, img));
  // Sweep the greys rather than testing the two extremes: the best text
  // for a pair of grounds is usually neither black nor white.
  let best = 0;
  for (let v = 0; v < 256; v += 2) {
    const c: RGB = [v / 255, v / 255, v / 255];
    best = Math.max(best, Math.min(...grounds.map((g) => contrastRatio(c, g))));
  }
  return best < AA_TEXT;
}

function hexToRgba(hex: string, alpha: number): string {
  const h = (hex || '').replace('#', '');
  if (h.length < 6) return `rgba(10,10,10,${alpha})`;
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// Hero photo as the HEADER's background (not a separate strip above it):
// the image covers the band under a scrim so the title/headline/perks stay
// readable.  With a custom Header colour the scrim IS that colour (brand
// tint with the photo showing through); without one it's a neutral dark.
// Scoped text vars follow the scrim so contrast is guaranteed either way.
export function heroHeaderStyle(imageUrl: string, headerColor?: string): import('react').CSSProperties {
  const header = headerColor ? parseHex(headerColor) : null;
  const tint: RGB = header ?? [10 / 255, 10 / 255, 10 / 255];
  const alpha = header ? 0.78 : 0.55;
  const scrim = header ? hexToRgba(headerColor!, alpha) : 'rgba(10,10,10,0.55)';
  // The ground under this text is not a colour — it is the scrim over
  // whatever photo the carrier uploaded, and we have never seen it. So
  // take the two extremes it could be, a white frame and a black one,
  // and satisfy the harder. Choosing against the scrim alone assumes an
  // image that happens to agree with it.
  const grounds: RGB[] = ([[1, 1, 1], [0, 0, 0]] as RGB[]).map((img) => over(tint, alpha, img));
  const worstOn = (c: RGB) => Math.min(...grounds.map((g) => contrastRatio(c, g)));
  const NEAR_BLACK: RGB = [10 / 255, 10 / 255, 10 / 255], WHITE: RGB = [1, 1, 1];
  const fgRgb = worstOn(NEAR_BLACK) >= worstOn(WHITE) ? NEAR_BLACK : WHITE;
  const hardest = grounds.reduce((a, b) =>
    contrastRatio(fgRgb, a) <= contrastRatio(fgRgb, b) ? a : b);
  return {
    backgroundImage: `linear-gradient(${scrim}, ${scrim}), url("${imageUrl}")`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
    ['--foreground']: toHex(fgRgb),
    // Was a flat rgba(255,255,255,0.78) / rgba(0,0,0,0.62) pair, which
    // measured under AA on 48.5% of header colours: an alpha is not a
    // contrast, and the same alpha over a pale tint and a deep one gives
    // two very different readings.
    //
    // This still leaves 41.6% of header colours under AA here, and that
    // number is NOT a defect to chase — it is the scrim. Sweeping every
    // possible text lightness against both photo extremes, only 60.1% of
    // header colours admit ANY text that clears 4.5:1 over an arbitrary
    // photo at 0.78 opacity. So 39.9% is unreachable and the derivation
    // above is within 1.7 points of the best that exists.
    //
    // The remedy is opacity, and it is a design call rather than an
    // arithmetic one, because it trades away the photograph the carrier
    // uploaded:
    //     0.78 -> 60.1%   0.85 -> 74.6%   0.90 -> 84.4%
    //     0.94 -> 92.0%   1.00 -> 100%  (no photo left)
    // A per-colour adaptive scrim — dim only as far as legibility needs
    // — would take most of it back without a fixed cost. Not done here:
    // it changes how every existing hero looks.
    ['--muted-foreground']:
      toHex(clampLightness(mixOklab(fgRgb, 78, hardest), hardest, AA_TEXT).rgb),
  } as import('react').CSSProperties;
}

// Style for an element whose BACKGROUND is a custom carrier colour (the
// header band, the page bg).  Sets the bg + readable text vars SCOPED to
// that element, so its own text/links stay legible while the rest of the
// form keeps the base theme.  Empty → undefined (base default).
export function onColorStyle(color?: string): import('react').CSSProperties | undefined {
  const c = color ? parseHex(color) : null;
  if (!c) return undefined;
  const fgRgb = readableOn(c);
  return {
    backgroundColor: color,
    ['--foreground']: toHex(fgRgb),
    // Same fix as the hero band: the 0.72 / 0.60 alphas were under AA on
    // 58.7% of carrier colours.
    ['--muted-foreground']: toHex(clampLightness(mixOklab(fgRgb, 72, c), c, AA_TEXT).rgb),
  } as import('react').CSSProperties;
}
