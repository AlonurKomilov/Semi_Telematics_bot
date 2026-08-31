/**
 * Colour maths that SHIPS — the same arithmetic the build-time guards use,
 * moved somewhere the browser can reach it.
 *
 * Why this file exists at all: every colour guard in this repo runs at
 * build time (`colour.test.ts`, the twelve chrome guards). They read
 * `index.css` off disk and check what we authored. The moment a theme
 * arrives at RUNTIME — an account's brand colour, a supplied palette —
 * the values move out from under those guards and all twelve stay green
 * while the app renders an unreadable page. A guard that cannot see the
 * value it guards is decoration.
 *
 * So the maths lives here, and `colour.test.ts` imports it. One
 * implementation, two callers: the build-time guard proves it correct
 * against every token we ship, and the runtime derivation uses the same
 * proven code on values nobody reviewed.
 *
 * The design rule this module exists to enforce: DERIVE AND CLAMP, never
 * accept and validate. A validator answers "is this theme legible?" —
 * and then someone has to decide what to do with "no". A clamp answers
 * "what is the closest legible theme to the one asked for?", which has
 * no failure branch, needs no error copy, and cannot be overridden by a
 * customer who likes their brand green more than they like reading.
 *
 * The instrument matters. Contrast here is WCAG 2.x relative luminance,
 * which is the right tool for "can this text be read". It is the WRONG
 * tool for "do these two colours look different" — that is ΔE, and
 * `colour.test.ts` keeps its own for the chart ramp. Do not reach for
 * `contrastRatio` to answer a perceptual-difference question; it inflates
 * badly on dark pairs.
 */

export type RGB = [number, number, number]; // sRGB, 0..1, gamma-ENCODED

// ── OKLab / OKLCH ↔ linear sRGB ─────────────────────────────────────
// Björn Ottosson's matrices. `oklabToLinear` takes OKLab and returns
// LINEAR sRGB (not encoded) because the gamut test below has to run in
// linear space.

const oklabToLinear = (L: number, a: number, b: number): RGB => {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ];
};

const linearToOklab = ([r, g, b]: RGB): RGB => {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ];
};

/** linear → gamma-encoded sRGB. */
const encode = (c: number) =>
  c <= 0.0031308 ? 12.92 * c : 1.055 * Math.max(c, 0) ** (1 / 2.4) - 0.055;
/** gamma-encoded sRGB → linear. */
const decode = (c: number) =>
  c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;

const inGamut = ([r, g, b]: RGB, eps = 1e-6) =>
  [r, g, b].every((c) => c >= -eps && c <= 1 + eps);
const clamp01 = ([r, g, b]: RGB): RGB =>
  [r, g, b].map((c) => Math.min(1, Math.max(0, c))) as RGB;

/**
 * CSS Color 4 gamut mapping: chroma reduction with a local clip inside a
 * 0.02 OKLab JND.
 *
 * This is not a nicety. An oklch value outside sRGB is not painted as
 * written — the browser maps it, so the colour on screen has a different
 * lightness and chroma than the file says. Any contrast sum done on the
 * authored value is arithmetic about a colour nobody sees. Every ratio in
 * this module is therefore computed AFTER mapping.
 *
 * `mapped` reports whether the authored colour needed mapping at all —
 * the gamut ratchet in `colour.test.ts` keys on it.
 */
export function oklchToSrgb(L: number, C: number, H: number): { rgb: RGB; mapped: boolean } {
  const h = (H * Math.PI) / 180;
  const lin = oklabToLinear(L, C * Math.cos(h), C * Math.sin(h));
  if (inGamut(lin)) return { rgb: clamp01(lin).map(encode) as RGB, mapped: false };
  if (L <= 0) return { rgb: [0, 0, 0], mapped: true };
  if (L >= 1) return { rgb: [1, 1, 1], mapped: true };
  let lo = 0, hi = C, best = clamp01(lin);
  const JND = 0.02;
  while (hi - lo > 1e-5) {
    const mid = (lo + hi) / 2;
    const cand = oklabToLinear(L, mid * Math.cos(h), mid * Math.sin(h));
    if (inGamut(cand)) { lo = mid; best = clamp01(cand); continue; }
    const clipped = clamp01(cand);
    const [dl, da, db] = linearToOklab(clipped);
    const d = Math.hypot(dl - L, da - mid * Math.cos(h), db - mid * Math.sin(h));
    if (d < JND) { best = clipped; lo = mid; } else { hi = mid; }
  }
  return { rgb: best.map(encode) as RGB, mapped: true };
}

/**
 * Bring an OKLCH triple into sRGB by reducing CHROMA ONLY — hue exact.
 *
 * This is the generation counterpart to `oklchToSrgb`, and the two must
 * not be confused:
 *
 *   `oklchToSrgb` models what a BROWSER paints when we author an
 *     out-of-gamut value in `index.css`. It has to be CSS Color 4,
 *     local clip and all, or the build-time guard is measuring a colour
 *     the screen will not show.
 *
 *   `srgbInGamut` CHOOSES a colour we will emit as a concrete value.
 *     Nothing is modelling a browser here, so the clip's ΔE advantage
 *     buys us nothing — and it costs hue, which is the one property a
 *     brand colour cannot lose. Desaturated orange still reads as
 *     orange; orange rotated 20 degrees is yellow.
 *
 * Measured over 18,700 clamps across five grounds, the local clip shifted
 * hue by up to 20.67 degrees (p99 17.81) where this shifts it by 0.02 —
 * and it saved no chroma worth having: p99 loss 0.1917 against 0.1936
 * here, a difference of 0.002. The clip was giving away the hue for
 * nothing.
 */
export function srgbInGamut(L: number, C: number, H: number): RGB {
  const h = (H * Math.PI) / 180;
  const at = (c: number) => oklabToLinear(L, c * Math.cos(h), c * Math.sin(h));
  // Most colours are already inside, and this runs inside a bisection
  // that runs inside a sweep — skipping the search when there is nothing
  // to search for is the difference between a 39-second guard and a
  // 17-second one.
  const c = inGamut(at(C)) ? C : maxChroma(L, H);
  return clamp01(at(c)).map(encode) as RGB;
}

/**
 * The most chroma sRGB can hold at this lightness and hue.
 *
 * Two callers, and they want it for opposite reasons. `srgbInGamut` uses
 * it as a ceiling, to bring a generated colour back inside. The gamut
 * ratchet in `colour.test.ts` uses it as a yardstick, to measure how far
 * OUTSIDE a token we authored has drifted — because a clipped token is a
 * colour nobody has seen, and nudging its chroma moves nothing except on
 * a P3 display.
 */
export function maxChroma(L: number, H: number): number {
  const h = (H * Math.PI) / 180;
  // 0.5 is comfortably past sRGB's widest oklch chroma (~0.32), so the
  // search always starts with a bracket.
  let lo = 0, hi = 0.5;
  // 32 halvings put the answer within 1e-10 of the gamut boundary. The
  // ratchet records chroma to four decimals and an 8-bit channel needs
  // about 1e-3, so the remaining iterations of a longer search buy
  // nothing and are paid for on every generated colour.
  for (let i = 0; i < 32; i++) {
    const mid = (lo + hi) / 2;
    if (inGamut(oklabToLinear(L, mid * Math.cos(h), mid * Math.sin(h)))) lo = mid;
    else hi = mid;
  }
  return lo;
}

/** The inverse, for taking a customer's hex apart. Hue is undefined at
 *  C≈0; it comes back 0 there rather than NaN so callers can round-trip. */
export function srgbToOklch(rgb: RGB): { L: number; C: number; H: number } {
  const [L, a, b] = linearToOklab(rgb.map(decode) as RGB);
  const C = Math.hypot(a, b);
  const H = C < 1e-7 ? 0 : ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360;
  return { L, C, H };
}

// ── WCAG ────────────────────────────────────────────────────────────

export const relLum = ([r, g, b]: RGB) =>
  0.2126 * decode(r) + 0.7152 * decode(g) + 0.0722 * decode(b);

/** WCAG 2.x contrast, 1..21. Symmetric — order does not matter. */
export const contrastRatio = (a: RGB, b: RGB) => {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

/** What the browser paints when `fg` at `alpha` sits on `bg`. An alpha
 *  fill measured against anything but its actual ground certifies real
 *  failures as clean — `--ok-bg` on a card is not the same colour as
 *  `--ok-bg` on the canvas. */
export const over = (fg: RGB, alpha: number, bg: RGB): RGB =>
  fg.map((c, i) => c * alpha + bg[i] * (1 - alpha)) as RGB;

/** WCAG floors. Text is 4.5; large text (≥18.66px bold / ≥24px) and
 *  non-text UI boundaries are 3.0 (WCAG 1.4.11). One number for both
 *  either drowns in 1.2:1 hairlines or misses the label on a button. */
export const AA_TEXT = 4.5;
export const AA_LARGE = 3;
export const AAA_TEXT = 7;

// ── hex ─────────────────────────────────────────────────────────────

/** `#rgb`, `#rrggbb`, with or without the hash. Null on anything else —
 *  this parses CUSTOMER input, so a bad value must be detectable rather
 *  than silently becoming black. */
export function parseHex(hex: string): RGB | null {
  const h = (hex || '').trim().replace(/^#/, '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255) as RGB;
}

export const toHex = (rgb: RGB): string =>
  '#' + rgb.map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)
    .toString(16).padStart(2, '0')).join('');

// ── the clamps ──────────────────────────────────────────────────────

/**
 * Round to the 8-bit triple sRGB actually ships.
 *
 * Load-bearing, not tidiness. The clamps below bisect in continuous
 * lightness and can land exactly ON the floor — and then `toHex` rounds
 * the answer to a channel step and it lands just under. Measured on the
 * public form's `--muted-foreground`, 57% of surfaces came out at
 * 4.48–4.4999:1 against a clamp that had honestly returned 4.50. A
 * guarantee about a float nobody paints is not a guarantee.
 */
const q8 = (c: RGB): RGB =>
  c.map((x) => Math.round(Math.min(1, Math.max(0, x)) * 255) / 255) as RGB;

const WHITE: RGB = [1, 1, 1];
const NEAR_BLACK: RGB = [10 / 255, 10 / 255, 10 / 255];

/**
 * The best of near-black and white on this ground, decided by MEASUREMENT.
 *
 * The predecessor of this function used the YIQ brightness formula
 * (0.299/0.587/0.114) on gamma-encoded bytes with a 0.6 threshold. Both
 * halves of that are wrong for the question: YIQ weights green 0.587
 * where luminance weights it 0.7152, and skipping the decode understates
 * bright colours. Sampled over the sRGB cube at step 3 (636,056
 * surfaces) it returned the WORSE of the two choices on 29.9% of them,
 * and its own pick fell under 4.5:1 on 30.5%. Worst single case: on
 * `#00ff1b` it chose white at 1.37:1 where black gave 14.44:1.
 *
 * Contrast against a fixed ground is V-shaped in the text's luminance
 * with its minimum at the ground, so the maximum over any range is at an
 * endpoint — which is why comparing the two extremes is not a heuristic
 * here but the exact answer.
 */
export function readableOn(bg: RGB): RGB {
  return contrastRatio(NEAR_BLACK, bg) >= contrastRatio(WHITE, bg) ? NEAR_BLACK : WHITE;
}

/**
 * Move `fg` along lightness — hue and chroma held — until it clears
 * `floor` against `bg`.
 *
 * This is the workhorse of a derived palette: a token computed from a
 * seed lands wherever the seed puts it, and this pulls it back to
 * legible without discarding the hue that made it the customer's colour.
 *
 * Direction is chosen first (away from the ground's luminance, toward
 * whichever end has more headroom), then bisected — valid because on
 * either side of the ground the ratio is monotonic in L. Every candidate
 * is gamut-mapped before it is measured, since mapping changes the
 * luminance that will actually be painted.
 *
 * `met` is false when even the endpoint falls short. That is not a bug
 * to retry: it means no colour of this hue reaches the floor on this
 * ground, and the caller's next move is `clampSurface`, not a smaller
 * step.
 *
 * At AA it cannot happen, and that is worth stating as a guarantee
 * rather than leaving as a surprise. Chroma reduces to zero at both ends
 * of lightness, so the endpoints are pure black and pure white for every
 * hue. The worst possible ground is the one where those two are equally
 * bad — relative luminance 0.17913 — and even there the better of them
 * gives 4.5826:1. So for any floor at or below 4.58, `met` is always
 * true: the engine can ALWAYS make text legible on a supplied colour.
 * Above it (AAA's 7:1) the branch is live and real.
 */
export function clampLightness(fg: RGB, bg: RGB, floor: number): { rgb: RGB; met: boolean } {
  if (contrastRatio(q8(fg), bg) >= floor) return { rgb: fg, met: true };
  const { C, H } = srgbToOklch(fg);
  const paint = (L: number) => q8(srgbInGamut(L, C, H));
  // Direction by MEASUREMENT, not by a lightness proxy.
  //
  // This used to read `srgbToOklch(bg).L > 0.5 ? 0 : 1` — darken on a
  // light ground, lighten on a dark one — which is wrong for exactly the
  // colours a customer picks. OKLab lightness and WCAG luminance
  // disagree on saturated hues, worst of all on blue, whose luminance
  // weight is 0.0722. #0048f8 has an OKLab L of 0.505, so the proxy
  // called it light and darkened; that reaches 3.28:1 where lightening
  // reaches 6.40. A brand blue is not an edge case.
  //
  // Two evaluations settle it exactly, and they also restore the
  // guarantee below: the better endpoint is now always the one tried.
  const end = contrastRatio(paint(0), bg) >= contrastRatio(paint(1), bg) ? 0 : 1;
  if (contrastRatio(paint(end), bg) < floor) return { rgb: paint(end), met: false };
  let lo = end, hi = srgbToOklch(fg).L;
  // `lo` always meets the floor, `hi` may not; converge on the value
  // closest to the one asked for that still does. 24 halvings resolve L
  // to 6e-8 — an 8-bit channel step is 4e-3, so this is already far past
  // anything paintable, and every extra iteration re-runs a gamut map.
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    if (contrastRatio(paint(mid), bg) >= floor) lo = mid; else hi = mid;
  }
  return { rgb: paint(lo), met: true };
}

/**
 * Push a SURFACE until some text can be read on it.
 *
 * For 1.1% of the sRGB cube neither near-black nor white reaches 4.5:1 —
 * and since those are the luminance extremes, no colour does. A
 * mid-tone brand green is not a text problem to solve; the surface
 * itself has to move. Hue and chroma are held, so the result is still
 * recognisably the colour that was asked for.
 */
export function clampSurface(bg: RGB, floor: number = AA_TEXT): { rgb: RGB; moved: boolean } {
  const best = (c: RGB) => Math.max(contrastRatio(NEAR_BLACK, c), contrastRatio(WHITE, c));
  if (best(bg) >= floor) return { rgb: bg, moved: false };
  const { L, C, H } = srgbToOklch(bg);
  const paint = (x: number) => q8(srgbInGamut(x, C, H));
  // Whichever end is nearer — a surface just under the bar should not
  // jump across the middle to fix a small shortfall.
  const dirs = L > 0.5 ? [1, 0] : [0, 1];
  for (const end of dirs) {
    if (best(paint(end)) < floor) continue;
    let lo = end, hi = L;
    for (let i = 0; i < 24; i++) {
      const mid = (lo + hi) / 2;
      if (best(paint(mid)) >= floor) lo = mid; else hi = mid;
    }
    return { rgb: paint(lo), moved: true };
  }
  return { rgb: paint(L > 0.5 ? 1 : 0), moved: true };
}
