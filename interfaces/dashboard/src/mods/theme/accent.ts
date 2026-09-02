/**
 * One colour a person picked, into the accent the app can actually wear.
 *
 * The curated packs in `catalogue.ts` were each tuned by hand: a light
 * value and a dark value, checked against the canvas, against the ink
 * that sits on them, and against the four semantic tones. "Boshqa rang"
 * hands one hex to a machine and asks for the same guarantees. This file
 * is that machine, and it is deliberately allowed to say no.
 *
 * Two things have to happen to a picked colour.
 *
 * **It has to join the band.** A hex tuned to read on white is too dark
 * to read on near-black and the reverse — which is why every pack ships
 * TWO seeds. A person picks once, so the lightness is re-set per mode
 * and only the hue and chroma survive. That is not a liberty: it is the
 * same edit the pack authors made by hand, and skipping it is how you
 * get a "custom accent" that is invisible in one mode.
 *
 * **It has to not be a status colour.** `--ok`, `--warn`, `--danger` and
 * `--info` mean something. A primary button the colour of `--danger` is
 * not a styling choice, it is a lie about what the button does — and
 * roughly a fifth of the light-mode hue wheel sits close enough to one
 * of them to read as it. So a picked colour is measured, nudged if a
 * small move rescues it, and refused if it cannot be.
 *
 * Refusing is the point. The alternative is a picker that accepts
 * everything and quietly ships a success-green Save button.
 */
import {
  parseHex, toHex, srgbToOklch, srgbInGamut, oklchToSrgb, distance,
  type RGB,
} from './contrast';

export type AccentMode = 'dark' | 'light';

/**
 * The lightness an accent lands on, per mode.
 *
 * Measured off the seeds that ship rather than chosen: blue, purple and
 * azure are all 0.52 light and 0.62 dark, to three decimals. Green is
 * the exception in both directions and is not evidence — see
 * `accent.test.ts`, which pins the band against the packs so a future
 * pack cannot drift away from it unnoticed.
 */
export const ACCENT_BAND: Readonly<Record<AccentMode, number>> = {
  light: 0.52,
  dark: 0.62,
};

/**
 * The four tones a primary must not be mistaken for, per mode.
 *
 * A copy of what `index.css` declares, because this runs in the browser
 * against a colour that does not exist until someone types it, and the
 * stylesheet is not readable from here. `accent.test.ts` reads the CSS
 * off disk and fails if the two disagree — the copy is allowed, the
 * drift is not.
 */
export const TONES: Readonly<Record<AccentMode, Readonly<Record<string, readonly [number, number, number]>>>> = {
  light: {
    ok:     [0.49, 0.132, 150],
    warn:   [0.50, 0.11, 70],
    danger: [0.52, 0.20, 25],
    info:   [0.49, 0.158, 255],
  },
  dark: {
    ok:     [0.76, 0.17, 150],
    warn:   [0.82, 0.14, 80],
    danger: [0.76, 0.14, 22],
    info:   [0.78, 0.113, 250],
  },
};

/**
 * How far from a tone is far enough.
 *
 * A literal, and NOT the separation our own packs happen to have. The
 * first version of this constant was 5.7, which is where the closest
 * shipped pair sits — and a floor of 5.7 accepts `--ok` itself as an
 * accent, measured. A gate that admits the thing it exists to exclude is
 * not a gate.
 *
 * 10 is where dE2000 stops meaning "a shade of" and starts meaning "a
 * different colour" at a glance, which is the question actually being
 * asked: can somebody tell a primary button from a status badge without
 * comparing them side by side. Measured across the whole wheel it costs
 * almost nothing — in dark mode nothing at all, because the dark tones
 * live at L 0.76-0.82 and the accent band is 0.62, so lightness alone
 * separates them by more than 13. In light mode 229 of 360 hues pass
 * untouched, 121 more clear the floor after a nudge, and 10 are refused.
 *
 * The curated packs do NOT clear this. Light blue sits 6.49 from
 * `--info` and light green 5.71 from `--ok`, so the four colours we ship
 * would all fail the gate we impose on a colour somebody picks. That is
 * a fact about the shipped light palette, recorded in `accent.test.ts`
 * rather than dissolved by lowering the number until it stops being
 * true. The packs are hand-authored and reach the page as CSS, so
 * nothing here enforces against them; the inconsistency is real and it
 * is the owner's to settle.
 */
export const TONE_FLOOR = 10;

/**
 * How far lightness may move to escape a tone.
 *
 * Bounded, because past a point the answer stops being the colour that
 * was asked for. Green's own exception is 0.04; twice that is enough to
 * clear every hue that can be cleared at all, and a hue that needs more
 * is a hue that IS the tone.
 */
const NUDGE_LIMIT = 0.08;
const NUDGE_STEP = 0.01;

export interface AccentResult {
  /** The three tokens an accent block ships, plus the label that has to
   *  read on it. Null when the colour was refused. */
  tokens: Record<string, string> | null;
  /** The colour actually used, after the band and any nudge. */
  hex: string | null;
  /** Set when a nudge was needed — the tone it moved away from. */
  movedFrom?: string;
  /** Set when the colour was refused — the tone it could not escape. */
  collidesWith?: string;
}

/** The nearest tone, and how near. */
function nearestTone(rgb: RGB, mode: AccentMode): { name: string; d: number } {
  let name = '', d = Infinity;
  for (const [n, [L, C, H]] of Object.entries(TONES[mode])) {
    const t = distance(rgb, oklchToSrgb(L, C, H).rgb);
    if (t < d) { d = t; name = n; }
  }
  return { name, d };
}

/**
 * Put a picked colour in the band, then out of the tones' way.
 *
 * Returns the colour and, when it had to move, what it moved away from —
 * the caller shows that, because a colour that silently came back
 * different is worse than one that explained itself.
 */
export function fitAccent(hex: string, mode: AccentMode): {
  rgb: RGB | null; movedFrom?: string; collidesWith?: string;
} {
  const picked = parseHex(hex);
  if (!picked) return { rgb: null };

  const { C, H } = srgbToOklch(picked);
  const L0 = ACCENT_BAND[mode];
  const at = (L: number) => srgbInGamut(L, C, H);

  const base = at(L0);
  const first = nearestTone(base, mode);
  if (first.d >= TONE_FLOOR) return { rgb: base };

  // Walk both ways at once and take the first lightness that clears,
  // so the colour moves as little as it can — the same rule
  // `clampLightness` follows for text.
  for (let step = NUDGE_STEP; step <= NUDGE_LIMIT + 1e-9; step += NUDGE_STEP) {
    for (const L of [L0 - step, L0 + step]) {
      if (L <= 0.05 || L >= 0.95) continue;
      const c = at(L);
      if (nearestTone(c, mode).d >= TONE_FLOOR) return { rgb: c, movedFrom: first.name };
    }
  }
  return { rgb: null, collidesWith: first.name };
}

/**
 * The tokens a custom accent installs.
 *
 * FOUR, not three. The packs ship `--primary`, `--primary-hover` and
 * `--primary-text` and leave `--primary-foreground` to the base, because
 * one label colour happens to work for all four of them. A colour nobody
 * checked cannot borrow that luck, so the label is measured here and
 * shipped with the rest.
 *
 * `--chart-1` is NOT here. Slot 1 follows the accent in the presets, but
 * only because each preset was measured against the other four slots.
 * A custom colour has not been, so the ramp keeps its base blue rather
 * than gaining a series that might be indistinguishable from another.
 */
export function accentTokens(hex: string, mode: AccentMode): AccentResult {
  const fit = fitAccent(hex, mode);
  if (!fit.rgb) return { tokens: null, hex: null, collidesWith: fit.collidesWith };

  const { L, C, H } = srgbToOklch(fit.rgb);
  const primary = toHex(fit.rgb);

  // Hover moves AWAY from the label, so the pair can only get easier to
  // read when the pointer lands. Which way that is depends on whether
  // the label is dark, exactly as `derivePalette` decides it.
  const label = pickLabel(fit.rgb);
  const away = label[0] < 0.5 ? 1 : -1;
  const hover = toHex(srgbInGamut(Math.min(0.95, Math.max(0.05, L + away * 0.06)), C, H));

  // The accent AS TEXT: on a page canvas rather than on itself, so it
  // has to clear AA against the canvas instead of against its own label.
  const text = toHex(srgbInGamut(mode === 'light' ? Math.min(L, 0.50) : Math.max(L, 0.70), C, H));

  return {
    tokens: {
      '--primary': primary,
      '--primary-foreground': toHex(label),
      '--primary-hover': hover,
      '--primary-text': text,
    },
    hex: primary,
    movedFrom: fit.movedFrom,
  };
}

/** Black or white, whichever reads on this ground. The shipped inks, not
 *  the true extremes — `--primary-foreground` is #fafafa in the light
 *  theme, and pure white measures as a slightly harsher label. */
function pickLabel(bg: RGB): RGB {
  const l = srgbToOklch(bg).L;
  return l > 0.6 ? [0.09, 0.09, 0.09] : [0.98, 0.98, 0.98];
}
