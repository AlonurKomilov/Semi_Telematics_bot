import type { ItemMeta } from './store/items/meta';

/**
 * How fast the app moves, and how it eases.
 *
 * TWO DIMENSIONS, and the second is why this is an item rather than a
 * setting. The scale is a multiplier every duration rides
 * (`calc(<literal> * var(--motion-scale))`), so three named speeds were
 * all there was to say. The CURVE is character: the same 200ms reads as
 * mechanical, or as something settling into place, depending on it.
 * A pack that ships both is saying how its world moves.
 *
 * The scale lives HERE as well as in the CSS, like an accent's seed:
 * the readout on the panel prints a percentage, and a percentage cannot
 * be read back out of a stylesheet. `theme/motion.test.ts` holds the two
 * equal, so the number cannot drift from the rule it describes.
 *
 * NOTHING HERE OUTRANKS THE FLOOR. `prefers-reduced-motion` ends the
 * loop with the only `!important` in the stylesheet, and it is guarded
 * as the only one in both directions — a motion item can dial a scale,
 * it cannot dial away somebody's vestibular system.
 */
export interface MotionPack extends ItemMeta {
  /** What every duration is multiplied by. Above 1 is slower. */
  readonly scale: number;
  /** Whether it ships its own curve. Presentation only — the CSS is
   *  what applies it; this says the item HAS one, for the store tile. */
  readonly eases?: true;
}

// The items — the list and the CSS — live in `mods/store/items/motion/`.
// This file is the contract one has to keep.
