/**
 * The grounds a person may pick — the page, the cards, the sidebar.
 *
 * A ground is a SEED, never a token: the page already works this way
 * (`theme.canvas` is one hex, and `derivePalette` turns it into
 * twenty-four values), and these two are the same decision one plane
 * over. That is the whole reason they can exist at all — every value
 * they install was derived by a function this codebase already
 * measures, rather than typed by somebody into a box.
 *
 * The gate is `fitCanvas`, unchanged and for the same reason: a badge,
 * a chip and an alert dot appear on a card and in the sidebar exactly
 * as they appear on the page, so a ground that makes one of the four
 * semantic tones unreadable is refused there too. Refusal happens on
 * the path that PAINTS — `groundTokens` returns nothing and the plane
 * falls back to the ladder — and the panel's message about it is a
 * courtesy, the way `CanvasChip`'s already is.
 */
import { deriveGround, GROUND_PLANES, type GroundId, type ThemeMode } from './palette';
import { fitCanvas, type CanvasResult } from './canvas';

export type { GroundId };

export interface Ground {
  readonly id: GroundId;
  readonly label: string;
  /** One line under the chip: what this plane IS, in the words a person
   *  would use asking for it. */
  readonly description: string;
}

/**
 * Two, because these are the two people ask for in words: "a dark
 * sidebar", "grey cards". The page is not in this list — it is
 * `theme.canvas`, it shipped first, and it derives every plane that has
 * no seed of its own.
 */
export const GROUNDS: readonly Ground[] = [
  // "Cards", not "Panels": this app already calls two OVERLAYS panels —
  // the assistant's and the one this control is sitting in — and a word
  // that means two things a few pixels apart is the collision to avoid.
  // Not "surfaces" in the copy either: the row above this one offers
  // Live Map, Loads and Work Orders, and those are what `surfaces` names
  // everywhere else in this engine.
  { id: 'card', label: 'Cards', description: 'The boxes the work sits in, and the dialogs above them' },
  // FRAME, not "Sidebar", and the id stays `sidebar` on purpose.
  //
  // The word is the one Wallpaper already uses for this region, because
  // one region with two names is two regions to a person reading the
  // panel. It is also the accurate half of the trade: these tokens paint
  // the rail, the header AND the gutters — everything `chrome-pane`
  // covers — so "Sidebar" named a third of what the colour changes.
  //
  // The ID keeps the token family's own name (`--sidebar-*`), which is
  // what the stylesheet calls them and what a stored value already says.
  // Size's "Sidebar" is a different thing and keeps its word: it scales
  // the nav rail alone, which is exactly what it sounds like.
  { id: 'sidebar', label: 'Frame', description: 'The rail, the header and the gutters the page sits in' },
];

export const GROUND_IDS: readonly GroundId[] = GROUNDS.map((g) => g.id);

export const groundById = (id: string): Ground | undefined =>
  GROUNDS.find((g) => g.id === id);

/**
 * One ground, gated and derived.
 *
 * NO WALLPAPER CHECK, and that is measured rather than assumed. A page
 * canvas gets one — `paletteTokens` refuses a canvas whose sidebar
 * cannot carry the frame pattern's strongest stop, and 21 of 96 wearable
 * dark canvases fail it — because that sidebar wears the PAGE's ink,
 * picked for the page. A seeded plane wears its OWN: `pickInk` takes the
 * better of the two shipped inks and falls back to the true extremes
 * when neither clears AA on the plane itself, which leaves enough
 * headroom that a fifth of the accent laid over it cannot spend it.
 * Swept over every wearable seed — 764 of them, both modes, all four
 * accents — the worst case is 5.27:1 against a floor of 4.5, and
 * `grounds.test.ts` holds that number rather than this paragraph.
 */
export function groundTokens(id: GroundId, hex: string, mode: ThemeMode): CanvasResult {
  const fit = fitCanvas(hex, mode);
  if (!fit.rgb) return { tokens: null, breaks: fit.breaks, ratio: fit.ratio };
  const tokens = deriveGround(id, hex, mode);
  return tokens ? { tokens } : { tokens: null };
}

/** Every token any ground can install — for the guards, and for anyone
 *  asking what a ground is allowed to reach. */
export const GROUND_TOKENS: readonly string[] =
  Object.values(GROUND_PLANES).flatMap((t) => [...t]);
