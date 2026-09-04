/**
 * A whole palette from two colours a person picked.
 *
 * `accent.ts` fits ONE colour and installs four tokens; this fits the
 * page itself and installs twenty-four — surfaces, their inks,
 * boundaries, the sidebar and the accent family. `derivePalette` already
 * does the hard half and is already proven: `palette.test.ts` sweeps the
 * seed space and holds body text legible on any canvas, the accent
 * legible both as text and as a button, and the dark elevation ladder
 * off the canvas.
 *
 * What is stored is the SEED, never the tokens. A palette computed for
 * dark mode and then worn in light mode is unreadable, and storing the
 * output is how that happens — the same trap `brand` avoids by storing
 * one hex and re-deriving per mode.
 *
 * THE GAP THIS FILE EXISTS TO CLOSE. `derivePalette` guarantees its own
 * output and deliberately reaches nothing else: the semantic tones
 * (`--ok`, `--warn`, `--danger`, `--info`), the chart ramp and the
 * structural axes are not seed-derived, and `palette.test.ts` asserts
 * they are left alone — a red that means danger cannot also be
 * somebody's brand red. But those tones follow the MODE, not the canvas,
 * so a canvas chosen against its mode leaves them sitting on a ground
 * they were never measured against. Measured, with the shipped values:
 *
 *   light mode · navy canvas   --info  2.49:1
 *   light mode · brown canvas  --info  2.11:1
 *   dark mode  · white canvas  --warn  1.77:1
 *   dark mode  · cream canvas  --warn  1.56:1
 *
 * All four below AA-large, on colours nothing else would have stopped.
 * So a canvas is REFUSED when it makes a tone unreadable — not nudged.
 * Nudging an accent moves one colour; nudging a canvas moves the whole
 * palette, and a person who asked for navy and got slate would be right
 * to call that broken.
 */
import {
  parseHex, oklchToSrgb, contrastRatio, AA_LARGE, type RGB,
} from './contrast';
import { derivePalette, DERIVED_TOKENS } from './palette';
import { TONES, type AccentMode } from './accent';

/**
 * The canvas each mode already paints, as a hex.
 *
 * A SECOND copy of what `:root` / `.dark` declare as `--background`,
 * and it exists for one reason: `<input type="color">` needs a concrete
 * hex to open on, and a picker that opens on black when the page is
 * white is the blank-decision problem the accent picker already had.
 * The stylesheet's value is oklch and cannot be handed to the control.
 *
 * `canvas.test.ts` parses `index.css` and fails when the two disagree —
 * the same bargain `MOTION_SCALE` and the font previews already make.
 * These are SEEDS, which is why this file carries literal colour:
 * `mods/catalogue.ts` holds the accent seeds for exactly the same reason.
 */
export const CANVAS_SEED: Readonly<Record<AccentMode, string>> = {
  light: '#ffffff',
  // #030303, not the #0a0a0a a reader would guess: the dark canvas is
  // oklch(0.10 0 0), and the drift test below caught the guess on its
  // first run. That is the whole reason it parses the stylesheet.
  dark: '#030303',
};

export interface CanvasResult {
  /** The tokens to install, or null when the canvas was refused. */
  tokens: Record<string, string> | null;
  /** The tone the canvas would have made unreadable. */
  breaks?: string;
  /** How much contrast that tone had, for the message to be specific. */
  ratio?: number;
}

const toneRgb = (mode: AccentMode, name: string): RGB => {
  const [L, C, H] = TONES[mode][name];
  return oklchToSrgb(L, C, H).rgb;
};

/**
 * The tone a canvas treats worst, and by how much.
 *
 * Exported because the panel says the name out loud — "the warning
 * colour would not be readable on that" is a sentence somebody can act
 * on; "invalid colour" is not.
 */
export function worstTone(canvas: RGB, mode: AccentMode): { name: string; ratio: number } {
  let name = '', ratio = Infinity;
  for (const t of Object.keys(TONES[mode])) {
    const r = contrastRatio(toneRgb(mode, t), canvas);
    if (r < ratio) { ratio = r; name = t; }
  }
  return { name, ratio };
}

/**
 * Whether a canvas may be worn in this mode.
 *
 * AA-large rather than AA-text: the tones appear as badge grounds, chart
 * strokes and icon fills far more often than as body copy, and holding
 * them to the body-copy floor would refuse most of the interesting
 * colours for a rule that does not describe how they are used.
 */
export function fitCanvas(hex: string, mode: AccentMode): {
  rgb: RGB | null; breaks?: string; ratio?: number;
} {
  const rgb = parseHex(hex);
  if (!rgb) return { rgb: null };
  const { name, ratio } = worstTone(rgb, mode);
  if (ratio < AA_LARGE) return { rgb: null, breaks: name, ratio };
  return { rgb };
}

/**
 * The tokens a chosen canvas installs.
 *
 * `brand` is resolved by the CALLER — a custom accent if one is picked,
 * otherwise the seed of the pack in force — so this file stays pure of
 * the catalogue and can be tested on two hexes.
 */
export function paletteTokens(
  canvas: string,
  brand: string,
  mode: AccentMode,
): CanvasResult {
  const fit = fitCanvas(canvas, mode);
  if (!fit.rgb) return { tokens: null, breaks: fit.breaks, ratio: fit.ratio };
  const palette = derivePalette({ mode, canvas, brand });
  // `derivePalette` returns null only on an unparseable seed, and the
  // canvas has already parsed — so this guards the BRAND the caller
  // resolved, which is the one it did not check.
  return palette ? { tokens: palette } : { tokens: null };
}


/**
 * The accent family, which a SURFACE may not touch.
 *
 * A per-place accent would have to out-rank the `[data-accent]` blocks
 * the way the global one does, and the `:not([data-mod-accent])`
 * stand-down that makes that work is written per BLOCK rather than per
 * surface — so a scoped accent would lose under purple, green and azure
 * while appearing to work under blue. That is the exact shape of the
 * bug `accentCascade.test.ts` exists for.
 *
 * It is also the right product answer independently: the accent is the
 * brand, and a brand that changes between pages is not one.
 */
const ACCENT_FAMILY = [
  '--primary', '--primary-foreground', '--primary-hover', '--primary-text',
] as const;

/** Everything a surface MAY set: the derived palette minus the accent. */
export const SURFACE_TOKENS = DERIVED_TOKENS
  .filter((t) => !(ACCENT_FAMILY as readonly string[]).includes(t));

/**
 * One place's colours, derived from its own canvas.
 *
 * The brand still goes in — `derivePalette` needs one and some derived
 * inks are measured against it — but the accent family comes straight
 * back out, so what a surface installs is surfaces, inks, boundaries
 * and the sidebar. The page keeps the accent it always had.
 */
export function surfaceTokens(
  canvas: string,
  brand: string,
  mode: AccentMode,
): CanvasResult {
  const full = paletteTokens(canvas, brand, mode);
  if (!full.tokens) return full;
  const out: Record<string, string> = {};
  for (const t of SURFACE_TOKENS) if (full.tokens[t]) out[t] = full.tokens[t];
  return { tokens: out };
}
