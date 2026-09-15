/**
 * The ladders this app ships — one file each, listed here.
 *
 * RESOURCE, NOT ENGINE, the same split every pack folder in this tree
 * makes: `depth/index.ts` says what a ladder must BE and knows nothing
 * about which ones exist; this file is what exists. The engine imports
 * DOWN into here for its default and never holds a number of its own —
 * `palette.ts` takes the ladder as an argument.
 *
 * NOT UNDER `store/items/`, which is where every other pack folder
 * lives, and the reason is a fact about when a ladder acts rather than
 * tidiness. `derivePalette` runs only when a person has SEEDED A
 * CANVAS — `context.tsx` calls it behind that condition — so for
 * everybody who has not, a chosen ladder would change nothing at all.
 * `store/items/` is the shelf, and `items.test.ts` holds the shelf
 * equal to what the store lists: a folder there is a tile somebody can
 * take. A tile that does nothing for most of the people who take it is
 * worse than no tile, so this stays an engine INPUT until a second
 * ladder and a home for the choice exist — at which point the folder
 * moves there whole and registers.
 */
import type { DepthPack } from './index';
import { flat } from './flat';

export const DEPTH_PACKS: readonly DepthPack[] = [flat];

export const depthPackById = (id: string): DepthPack | undefined =>
  DEPTH_PACKS.find((p) => p.id === id);

/** The ladder in force when nobody has chosen one. Named rather than
 *  indexed, so a reader of the engine's call sites can see WHICH. */
export const DEFAULT_DEPTH = flat;
