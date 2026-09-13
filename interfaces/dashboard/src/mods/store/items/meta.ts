/**
 * What EVERY item carries, whatever its axis — the part a person reads
 * before choosing it, and the part a store tile would show.
 *
 * Three fields, all required, because these are the three every item
 * can honestly fill today. What an item is *made of* — a seed, a cue
 * table, a lighting model — is its own contract on top of this one.
 * What a PACK adds — author, version, installs, a price — belongs to
 * the pack that ships the item, not to the item:
 * fields on a contract that nothing fills are a promise the type keeps
 * for nobody. A preview is not a field either: a tile draws it from
 * the payload — the seed, the pack's own CSS, a glyph, a cue played —
 * and a stored copy of that is the swatch mistake again.
 *
 * `items.test.ts` sweeps every item on every axis against this.
 */
export interface ItemMeta {
  /** Stored, stamped on `<html>`, used as a CSS name — `[a-z][a-z0-9-]*`. */
  readonly id: string;
  /** Shown on the chip. English; pack names are not translated. */
  readonly label: string;
  /** One line under the label: what choosing it gets you. */
  readonly description: string;
}
