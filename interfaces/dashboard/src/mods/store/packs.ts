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
 * Packs that bring their own items. Empty today, and that is the honest
 * state: everything this app ships was drawn before packs existed, so
 * it all belongs to the base pack below. The first themed pack adds its
 * entry here and its item files beside the others, and the base pack
 * gives up exactly what the new one claims — no list to edit twice.
 */
const THEMED: readonly Pack[] = [];

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
