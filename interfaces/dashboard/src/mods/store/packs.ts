/**
 * A pack — the thing a person installs.
 *
 * An ITEM is one axis's worth of a choice: a wallpaper, a cue set, an
 * accent. A PACK ships items, usually several axes at once, under one
 * name: install it and its wallpaper, its sounds and its colours all
 * arrive together; remove it and they all leave. That is the unit the
 * store sells, and the unit a person thinks in — nobody wants to
 * assemble a look out of ten separate decisions, and the ones who do
 * still can, because the pickers keep offering items one at a time.
 *
 * The pack owns the naming too. A themed pack's items carry the pack's
 * own name on every shelf — its wallpaper is "Budo", its keyboard is
 * "Budo" — so a person recognises where a thing came from without
 * reading anything.
 *
 * ONE PACK PER ITEM, held both ways by `packs.test.ts`: an item with no
 * pack could never be installed or removed, and an item claimed by two
 * would leave on the first removal and still look present.
 */
import { ITEM_AXES } from './items';
import { PUBLISHER } from './index';
import type { ItemMeta } from './items/meta';

export interface Pack extends ItemMeta {
  /** Stamped by the store, never self-declared — the house says who
   *  owns what, the same rule the item rows follow. */
  readonly publisher: string;
  /** What it ships, by axis. Every id is an item on that axis. */
  readonly items: Readonly<Record<string, readonly string[]>>;
  /** The pack that may not be taken off: it carries every axis's
   *  default, and an axis with nothing on it is one nobody can leave. */
  readonly base?: true;
}

/**
 * Packs beside the base one.
 *
 * The two prepared looks are each their own pack, and that is not a
 * technicality: a pack is what a person installs, and "give me the
 * whole thing ready" is exactly what these two are for. Each ships one
 * item — its preset — which sets five shelves at once out of what the
 * base pack already brought. A themed pack later will ship more: its
 * own wallpaper, its own cue set, and a preset that ties them together.
 *
 * Listed rather than derived from `MODS`, because a preset that arrives
 * as part of a themed pack belongs to THAT pack, and a rule of "every
 * preset is its own pack" would be wrong the day one does. A preset
 * nobody packs falls to the base pack, which is also correct.
 */
const THEMED: readonly Pack[] = [
  { id: 'cab', label: 'Cab', publisher: PUBLISHER, items: { mods: ['cab'] },
    description: 'Ready for a moving truck — bigger targets, a cue that cuts through road noise' },
  { id: 'wall', label: 'Wall', publisher: PUBLISHER, items: { mods: ['wall'] },
    description: 'Ready for a wall display — read from across the room' },
  /**
   * The first pack that brings its OWN items: a pattern, a cue set and
   * a keyboard, all called Night Haul on their shelves, plus the preset
   * that wears them together. It borrows the accent — the chart ramp
   * has no hue left that clears the tones under simulated colour
   * blindness — and a pack does not have to ship an item on an axis to
   * prepare that axis.
   */
  { id: 'night-haul', label: 'Night Haul', publisher: PUBLISHER,
    description: 'For the end of a long shift — low light, low sound, nothing in a hurry',
    items: { wallpaper: ['night-haul'], sound: ['night-haul'], keys: ['night-haul'],
      acts: ['night-haul'], motion: ['night-haul'], mods: ['night-haul'] } },
  /** The daylight half of the pair — its own horizon and its own air. */
  { id: 'long-haul', label: 'Long Haul', publisher: PUBLISHER,
    description: 'For the middle of a long day — the horizon, moving air, and nothing kept waiting',
    items: { wallpaper: ['long-haul'], ambience: ['wind'], mods: ['long-haul'] } },
  /** The one that is not a cab at all. */
  { id: 'desk', label: 'Desk', publisher: PUBLISHER,
    description: 'For a desk with other people at it — nothing patterned, nothing loud, nothing moving sideways',
    items: { ambience: ['room'], mods: ['desk'] } },
];

const key = (axis: string, id: string) => `${axis}/${id}`;
const CLAIMED = new Set(
  THEMED.flatMap((p) => Object.entries(p.items)
    .flatMap(([axis, ids]) => ids.map((id) => key(axis, id)))),
);

/**
 * What the app was drawn in — GX's own shape, where the browser's
 * original look is a pack like any other, sitting first.
 *
 * DERIVED, never listed: it ships every item no themed pack claims. A
 * hand-written list here would be a second catalogue, and it would fall
 * behind on the first item anybody adds.
 */
const CLASSIC: Pack = {
  // Bare, like every id the platform itself ships — a third party's get
  // a `<publisher>-` prefix at the publish gate. Not "4truck-classic":
  // an id that opens with a digit is one no CSS selector can name, and
  // these ids are stored values with a long life ahead of them.
  id: 'classic',
  label: '4truck Classic',
  description: 'The look this app was drawn in — every item it shipped with.',
  publisher: PUBLISHER,
  base: true,
  items: Object.fromEntries(ITEM_AXES.map((a) => [
    a.axis,
    a.items.map((i) => i.id).filter((id) => !CLAIMED.has(key(a.axis, id))),
  ])),
};

export const PACKS: readonly Pack[] = [CLASSIC, ...THEMED];

export const packById = (id: string): Pack | undefined =>
  PACKS.find((p) => p.id === id);

/** The pack an item came from. Every item has exactly one. */
export const packOf = (axis: string, id: string): Pack | undefined =>
  PACKS.find((p) => (p.items[axis] ?? []).includes(id));

/** Whether a pack may be taken off at all — the base one may not. */
export const removable = (id: string): boolean => !packById(id)?.base;
