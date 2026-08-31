/**
 * A whole palette from a seed — our design system's RELATIONSHIPS applied
 * to somebody else's colour.
 *
 * The distinction matters, because "derive a theme from one hex" invites
 * the wrong architecture. What a customer supplies is a canvas and an
 * accent. What they do NOT supply, and must not, is the ladder between
 * surfaces, the contrast the secondary ink holds, or which four hues mean
 * ok / warn / danger / info. Those encode design decisions and, in the
 * tones' case, meaning; handing them over is how a themeable product
 * stops being a product.
 *
 * So every constant below was MEASURED out of the themes we already ship
 * — index.css's `:root` and `.dark` — rather than chosen. Feeding our own
 * canvases back in reproduces our own themes, and `palette.test.ts`
 * asserts exactly that. A rule that cannot regenerate the design it came
 * from is not the rule the design was using.
 *
 * Three things it will not do, on purpose:
 *
 *   - It does not touch the tone system (`--ok`, `--warn`, `--danger`,
 *     `--info` and their washes). A red that means "danger" cannot also
 *     be a carrier's brand red.
 *   - It does not touch the chart ramp. Series separation is a ΔE
 *     property of the whole set; you cannot derive slot 4 from a seed
 *     without knowing slots 1-3 and 5.
 *   - It does not touch radius or the size axes. Those are their own
 *     axes and already distributable on their own.
 */
import {
  parseHex, toHex, srgbToOklch, srgbInGamut, contrastRatio,
  clampLightness, AA_TEXT, type RGB,
} from './contrast';

export type ThemeMode = 'dark' | 'light';

export interface ThemeSeed {
  mode: ThemeMode;
  /** `--background`: the page canvas. */
  canvas: string;
  /** `--primary`: the accent everything interactive is tinted with. */
  brand: string;
}

/**
 * The surface ladder, as OKLab lightness offsets from the canvas.
 *
 * Measured from index.css. The two modes are not mirror images and that
 * is a real design fact, not an oversight: light's canvas is pure white,
 * so a card cannot be brighter than the page and elevation is carried by
 * SHADOW — `--card` sits at +0.000 and only the recessed surfaces move.
 * Dark's canvas is near the floor, so the whole ladder rises off it.
 * Averaging the two, or deriving one from the other, would flatten the
 * light theme's cards into the page.
 */
type Plane = { dL: number; C: number };
const LADDER: Record<ThemeMode, Record<string, Plane>> = {
  light: {
    card:          { dL:  0.000, C: 0.000 },
    popover:       { dL:  0.000, C: 0.000 },
    secondary:     { dL: -0.030, C: 0.000 },
    muted:         { dL: -0.030, C: 0.000 },
    accent:        { dL: -0.030, C: 0.000 },
    sidebar:       { dL: -0.035, C: 0.004 },
    sidebarAccent: { dL: -0.065, C: 0.006 },
  },
  dark: {
    card:          { dL:  0.175, C: 0.000 },
    popover:       { dL:  0.220, C: 0.000 },
    secondary:     { dL:  0.200, C: 0.000 },
    muted:         { dL:  0.140, C: 0.000 },
    accent:        { dL:  0.240, C: 0.015 },
    sidebar:       { dL:  0.115, C: 0.022 },
    sidebarAccent: { dL:  0.200, C: 0.025 },
  },
};

/**
 * The sidebar plane is COOL, and that is a signature rather than an
 * accident: every one of its tokens carries a small chroma at hue 240 in
 * both themes, while `--muted` and `--secondary` sit at chroma exactly
 * zero. It does not follow the accent either — the purple and green
 * cells re-point only `--primary` and `--chart-1`, so the sidebar stays
 * cool under all six. Used as the fallback hue when the seed's canvas is
 * a pure grey and has no hue of its own to lend.
 */
const COOL = 240;

/**
 * Contrast targets, also measured. Only the secondary ink has one — the
 * primary ink is reused unchanged on every surface (in dark, `#fafafa`
 * is the foreground of the page, the card, the popover, the sidebar and
 * the accent alike; its contrast varies from 19.72 to 11.23 only because
 * the grounds differ). Deriving a separate ink per surface would be
 * inventing a rule the design does not have.
 */
const INK_TARGET: Record<ThemeMode, { muted: number; recessed: number }> = {
  // `--muted-foreground` on `--muted`, and the softened ink the recessed
  // planes carry. The second one is a light-theme detail that is easy to
  // miss: `--secondary-foreground`, `--accent-foreground` and
  // `--sidebar-accent-foreground` are #171717 where the page ink is
  // #0a0a0a — a ΔE of 5, deliberate, and gone if you reuse one ink
  // everywhere. Dark does reuse one ink, and expressing both as a
  // CONTRAST target rather than a colour is what lets one rule say so:
  // at 13.06 the dark answer is the page ink itself.
  light: { muted: 4.54, recessed: 16.42 },
  dark:  { muted: 6.16, recessed: 13.06 },
};

/**
 * `--primary-text` is the accent moved AWAY from the canvas — darker on
 * a light page, lighter on a dark one — not the accent clamped to a
 * floor. Measured: ΔL -0.040 light (5.76:1 → 6.88), +0.082 dark (5.40 →
 * 7.59). The dark cell also loses chroma, 0.206 → 0.153, and that falls
 * out for free: sRGB cannot hold that much blue at lightness 0.700, so
 * the gamut step does it.
 *
 * `--primary-hover` moves away from the LABEL, and our own themes are
 * the proof the rule is right rather than convenient — light has a white
 * label and hovers darker (-0.050), dark has a near-black one and hovers
 * lighter (+0.062).
 */
const PRIMARY_SHIFT: Record<ThemeMode, { text: number; hover: number }> = {
  light: { text: 0.040, hover: 0.050 },
  dark:  { text: 0.082, hover: 0.062 },
};
/** `--border` and `--input` against the canvas, and the sidebar's own. */
const BOUNDARY: Record<ThemeMode, { border: number; input: number; sidebar: number }> = {
  light: { border: 1.26, input: 1.26, sidebar: 1.18 },
  dark:  { border: 1.29, input: 1.57, sidebar: 1.33 },
};

/**
 * Not pure black and white: the inks our themes actually ship. Using the
 * true extremes measures a ΔE of 1.7 against `--foreground` and reads as
 * a harsher page.
 *
 * They are not the luminance extremes, though, so they carry no
 * guarantee of their own: the best of the two bottoms out at 4.3552:1
 * (at a ground of relative luminance 0.18098) and falls under AA on
 * 3.19% of canvases, where pure black and white fail on none. So
 * `pickInk` prefers these and escalates to the extremes only where they
 * will not do — the design ink everywhere it can be read, the standard
 * everywhere else.
 */
const INK_DARK: RGB = [10 / 255, 10 / 255, 10 / 255];    // #0a0a0a
const INK_LIGHT: RGB = [250 / 255, 250 / 255, 250 / 255]; // #fafafa

/**
 * The design's ink if it clears AA on this ground, the extreme if not.
 *
 * Escalation rather than a choice between two policies: #fafafa is what
 * our themes ship and what makes a page look like ours, but it is 4% of
 * the way off white and that costs real contrast on a mid ground. Using
 * it unconditionally puts 3.19% of canvases under AA; using pure white
 * unconditionally makes every page slightly harsher than the one we
 * designed. Preferring one and falling back to the other costs neither.
 */
const pickInk = (ground: RGB): RGB => {
  const shipped = contrastRatio(INK_DARK, ground) >= contrastRatio(INK_LIGHT, ground)
    ? INK_DARK : INK_LIGHT;
  if (contrastRatio(shipped, ground) >= AA_TEXT) return shipped;
  return shipped[0] < 0.5 ? [0, 0, 0] : [1, 1, 1];
};

/** Every custom property this function produces. Exported so a guard can
 *  assert the set has not silently grown into the tones or the ramp. */
export const DERIVED_TOKENS = [
  '--background', '--foreground',
  '--card', '--card-foreground', '--popover', '--popover-foreground',
  '--secondary', '--secondary-foreground', '--muted', '--muted-foreground',
  '--accent', '--accent-foreground',
  '--border', '--input',
  '--sidebar', '--sidebar-foreground', '--sidebar-accent',
  '--sidebar-accent-foreground', '--sidebar-border',
  '--primary', '--primary-foreground', '--primary-hover', '--primary-text',
] as const;

/**
 * The DIMMEST ink that still holds `target` against its ground.
 *
 * Not `clampLightness`, and the difference is the whole point of a
 * secondary ink. That function raises contrast to a floor and returns
 * anything already above it untouched — feed it the page ink and it
 * hands the page ink straight back at 19:1, which is not a secondary
 * anything. This walks the other way: from the ink toward the ground,
 * stopping the moment the target would be broken. The result is the
 * quietest legible value rather than the loudest.
 */
const dimTo = (ink: RGB, ground: RGB, target: number): RGB => {
  const I = srgbToOklch(ink), G = srgbToOklch(ground);
  const at = (t: number) => srgbInGamut(I.L + (G.L - I.L) * t, I.C, I.H);
  if (contrastRatio(ink, ground) < target) return ink;   // nothing to give
  let lo = 0, hi = 1;                                    // lo always holds
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    if (contrastRatio(at(mid), ground) >= target) lo = mid; else hi = mid;
  }
  return at(lo);
};

/** Step a surface along lightness, holding hue and chroma, staying in
 *  sRGB. A canvas already at the top of the range simply stops there —
 *  which is what our own light theme does. */
const step = (base: RGB, dL: number): RGB => {
  const { L, C, H } = srgbToOklch(base);
  return srgbInGamut(Math.min(1, Math.max(0, L + dL)), C, H);
};

/** A recessed plane: a lightness step plus this design's cool tint. A
 *  seed whose canvas already has a hue keeps it; a grey canvas borrows
 *  ours, which is how the neutral themes we ship come back out. */
const plane = (base: RGB, p: Plane): RGB => {
  const { L, C, H } = srgbToOklch(base);
  return srgbInGamut(
    Math.min(1, Math.max(0, L + p.dL)),
    p.C === 0 ? C : Math.max(C, p.C),
    C > 0.002 ? H : COOL,
  );
};

/**
 * @returns every derivable token as a hex string, or null if either seed
 *   is unparseable. Never a partial palette: half a theme applied over
 *   the other half is worse than none.
 */
export function derivePalette(seed: ThemeSeed): Record<string, string> | null {
  const canvas = parseHex(seed.canvas);
  const brand = parseHex(seed.brand);
  if (!canvas || !brand) return null;

  const L = LADDER[seed.mode], B = BOUNDARY[seed.mode];
  const T = INK_TARGET[seed.mode], S = PRIMARY_SHIFT[seed.mode];
  // The ink is picked by measurement rather than from `mode`, so a light
  // theme handed a dark canvas still gets readable text instead of a
  // theoretically-correct unreadable one.
  const ink = pickInk(canvas);
  /** Positive when the ink is lighter than the canvas — the direction
   *  "away from the page" points in, for everything that has to stand off
   *  it. */
  const away = srgbToOklch(ink).L >= srgbToOklch(canvas).L ? 1 : -1;
  const hex = (c: RGB) => toHex(c);

  const card = plane(canvas, L.card);
  const popover = plane(canvas, L.popover);
  const secondary = plane(canvas, L.secondary);
  const muted = plane(canvas, L.muted);
  const accent = plane(canvas, L.accent);
  const sidebar = plane(canvas, L.sidebar);
  const sidebarAccent = plane(canvas, L.sidebarAccent);

  /** A boundary is not text, so it gets a ratio rather than AA — and the
   *  ratio is OURS (1.26 light), not WCAG 1.4.11's 3:1. A themed page
   *  whose fields are outlined twice as hard as the dashboard's reads as
   *  a different product; both move together if that ever changes. */
  const edge = (ground: RGB, target: number) => {
    const g = srgbToOklch(ground);
    // Start from the ground and walk toward the ink until the hairline
    // shows, so a boundary always sits ON its own surface rather than on
    // the page — the sidebar's border is a shade of the sidebar.
    return hex(clampLightness(
      srgbInGamut(Math.min(1, Math.max(0, g.L + away * 0.08)), g.C, g.H), ground, target).rgb);
  };

  const brandL = srgbToOklch(brand);
  const shift = (dL: number) =>
    srgbInGamut(Math.min(1, Math.max(0, brandL.L + dL)), brandL.C, brandL.H);

  // The accent AS TEXT: moved away from the canvas, then held to AA as a
  // floor. The shift is what our design does; the clamp is what stops a
  // seed whose accent is too close to its canvas from relying on it.
  const primaryText = clampLightness(shift(away * S.text), canvas, AA_TEXT).rgb;

  // The label, and a hover that moves AWAY from it so the label can only
  // improve when the pointer lands.
  // The shipped inks, not the true extremes — `--primary-foreground` is
  // #fafafa in our light theme, and pure white measures ΔE 1.7 against
  // it as a slightly harsher label.
  const label = pickInk(brand);
  const labelIsDark = label[0] < 0.5;

  // ONE softened ink, computed once and reused — which is what the
  // design does. The three recessed planes all carry #171717 in light;
  // their contrasts differ (16.42 on --secondary, 14.81 on
  // --sidebar-accent) only because the grounds differ, so a per-surface
  // target would be reading the symptom and re-deriving three colours
  // where the design has one.
  const softInk = dimTo(ink, secondary, T.recessed);
  const hover = shift((labelIsDark ? 1 : -1) * S.hover);

  return {
    '--background': hex(canvas),
    '--foreground': hex(ink),
    '--card': hex(card),
    '--card-foreground': hex(ink),
    '--popover': hex(popover),
    '--popover-foreground': hex(ink),
    '--secondary': hex(secondary),
    '--secondary-foreground': hex(softInk),
    '--muted': hex(muted),
    '--muted-foreground': hex(dimTo(ink, muted, T.muted)),
    '--accent': hex(accent),
    '--accent-foreground': hex(softInk),
    '--border': edge(canvas, B.border),
    '--input': edge(canvas, B.input),
    '--sidebar': hex(sidebar),
    '--sidebar-foreground': hex(ink),
    '--sidebar-accent': hex(sidebarAccent),
    '--sidebar-accent-foreground': hex(softInk),
    '--sidebar-border': edge(sidebar, B.sidebar),
    '--primary': hex(brand),
    '--primary-foreground': hex(label),
    '--primary-hover': hex(hover),
    '--primary-text': hex(primaryText),
  };
}
