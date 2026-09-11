/**
 * The store — the source keeper.
 *
 * `mods/` is the ENGINE: it paints what a person chose, and it owns the
 * contracts a pack fills. It does not own WHAT THERE IS TO CHOOSE. That
 * is this folder: the pack sources live under it, and this file is the
 * catalogue of them.
 *
 * The store belongs to the platform. Not to an account, not to a
 * person: a pack is not tenant data, and no pack asset ever lands under
 * `account-{id}/` (the object-storage layout is for a customer's own
 * files). Someone tomorrow may be let in to publish here — they are
 * working in the house, the house is still ours.
 *
 * Which is why a pack does not say who published it. The STORE stamps
 * that, here, as it takes a pack into the catalogue — and `store.test.ts`
 * holds a pack file to declaring nothing of the kind. When a third
 * party is let in, their ids get a `<publisher>-` prefix at the publish
 * gate and the platform's own ids stay bare; an id is stored in a
 * device preference and used as a CSS `data-*` value, so an id that
 * changes later is a migration. None of these will have to change.
 *
 * What this file is NOT: a second list. Every row is derived from
 * `PACK_AXES`, so the catalogue cannot drift from the folders — the
 * store adds a stamp, never an entry.
 */
import { PACK_AXES } from './packs';
import type { PackMeta } from './packs/meta';

/** The house. One value today; the column exists because the stamp is
 *  the store's word, and a stamp with nobody to attribute is still the
 *  store saying "mine". */
export const PUBLISHER = '4truck';

export interface StoreRow extends PackMeta {
  /** The folder under the store, and the noun a tile is filed under. */
  readonly axis: string;
  /** Who this pack belongs to — stamped by the store, never self-declared. */
  readonly publisher: string;
}

export const STORE: readonly StoreRow[] = PACK_AXES.flatMap(({ axis, packs }) =>
  packs.map((p): StoreRow => ({
    id: p.id, label: p.label, description: p.description, axis, publisher: PUBLISHER,
  })),
);

/** Every axis the store carries, in catalogue order. */
export const STORE_AXES: readonly string[] = PACK_AXES.map((a) => a.axis);

export const rowsOf = (axis: string): readonly StoreRow[] => STORE.filter((r) => r.axis === axis);
export const idsOf = (axis: string): readonly string[] => rowsOf(axis).map((r) => r.id);
export const rowById = (axis: string, id: string): StoreRow | undefined =>
  STORE.find((r) => r.axis === axis && r.id === id);
