/**
 * How far the planes stand apart — the ladder, as a PACK.
 *
 * A palette is one seed and a set of steps away from it. The seed is
 * the person's (`theme.canvas`); the steps were a table inside
 * `theme/palette.ts`, which is the engine. That was the wrong side of
 * this repo's own line, written at the top of every resource folder
 * here: the engine keeps the contract and knows nothing about which
 * packs exist.
 *
 * The distinction the owner drew, and it is the right one: the LAMP is
 * the item. Taking a lamp, passing it through, measuring what it does
 * and refusing one that makes text unreadable — that is the engine.
 * `fitCanvas` stays exactly where it is and keeps refusing; what moved
 * is the numbers it measures.
 *
 * What this buys beyond tidiness: the light ladder currently says a
 * card is ZERO steps from the page, which is why every light surface in
 * this product is the same white and why glass has nothing to show
 * there. That is a design decision, and a design decision belongs
 * somewhere a person can change it — not compiled into the machine that
 * derives from it.
 */
import type { ItemMeta } from '../store/items/meta';
import type { ThemeMode } from '../theme/palette';

/**
 * The planes a ladder positions, in the order they stand.
 *
 * `card` and `popover` are what content sits on; `secondary`, `muted`
 * and `accent` are the tones inside it; the two `sidebar` entries are
 * the frame. The PAGE is not here — it IS the seed, which is why a
 * plane's step is measured from it.
 */
export const PLANES = [
  'card', 'popover', 'secondary', 'muted', 'accent', 'sidebar', 'sidebarAccent',
] as const;
export type PlaneName = (typeof PLANES)[number];

/**
 * One plane's distance from the seed.
 *
 * `dL` is signed toward "away from the page": positive lifts a plane
 * out of a dark canvas, negative settles it into a light one. `C` is
 * the chroma a plane carries of its own — the frame has a little, the
 * content planes have none, so a card never picks up a colour cast the
 * page did not ask for.
 */
export interface Plane {
  readonly dL: number;
  readonly C: number;
}

export interface DepthPack extends ItemMeta {
  readonly ladder: Readonly<Record<ThemeMode, Readonly<Record<PlaneName, Plane>>>>;
}
