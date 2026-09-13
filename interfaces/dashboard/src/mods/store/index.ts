/**
 * The store — the source keeper.
 *
 * `mods/` is the ENGINE: it paints what a person chose, and it owns the
 * contracts a pack fills. It does not own WHAT THERE IS TO CHOOSE. That
 * is this folder: the item sources live under it, and this file is the
 * catalogue of them.
 *
 * An ITEM is one axis's worth of a choice — a wallpaper, a cue set, an
 * accent. A PACK is what ships items: one name a person installs, and
 * every axis it brought with it.
 *
 * The store belongs to the platform. Not to an account, not to a
 * person: an item is not tenant data, and no pack asset ever lands under
 * `account-{id}/` (the object-storage layout is for a customer's own
 * files). Someone tomorrow may be let in to publish here — they are
 * working in the house, the house is still ours.
 *
 * Which is why an item does not say who published it. The STORE stamps
 * that, here, as it takes an item into the catalogue — and
 * `store.test.ts` holds an item file to declaring nothing of the kind. When a third
 * party is let in, their ids get a `<publisher>-` prefix at the publish
 * gate and the platform's own ids stay bare; an id is stored in a
 * device preference and used as a CSS `data-*` value, so an id that
 * changes later is a migration. None of these will have to change.
 *
 * What this file is NOT: a second list. Every row is derived from
 * `ITEM_AXES`, so the catalogue cannot drift from the folders — the
 * store adds a stamp, never an entry.
 */
import { ITEM_AXES } from './items';
import type { ItemMeta } from './items/meta';

/** The house. One value today; the column exists because the stamp is
 *  the store's word, and a stamp with nobody to attribute is still the
 *  store saying "mine". */
export const PUBLISHER = '4truck';

export interface ItemRow extends ItemMeta {
  /** The folder under the store, and the noun a tile is filed under. */
  readonly axis: string;
  /** Who this item belongs to — stamped by the store, never self-declared. */
  readonly publisher: string;
}

export const STORE: readonly ItemRow[] = ITEM_AXES.flatMap(({ axis, packs }) =>
  packs.map((p): ItemRow => ({
    id: p.id, label: p.label, description: p.description, axis, publisher: PUBLISHER,
  })),
);

/** Every axis the store carries, in catalogue order. */
export const STORE_AXES: readonly string[] = ITEM_AXES.map((a) => a.axis);

export const itemsOf = (axis: string): readonly ItemRow[] => STORE.filter((r) => r.axis === axis);
export const itemIdsOf = (axis: string): readonly string[] => itemsOf(axis).map((r) => r.id);
export const itemById = (axis: string, id: string): ItemRow | undefined =>
  STORE.find((r) => r.axis === axis && r.id === id);
