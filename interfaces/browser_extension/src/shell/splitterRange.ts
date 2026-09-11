/**
 * The arithmetic a Splitter's range is made of, kept out of the component
 * so it can be read and tested on its own — and because a component file
 * that exports a function loses fast refresh for the whole module.
 */
/** Neither region may be dragged away entirely.  A card at 0 hides the
 *  truck somebody just selected; a list at 0 hides the way to select
 *  another, and both leave the panel looking broken rather than
 *  configured. */
export const MIN_PCT = 20;
export const MAX_PCT = 80;

/**
 * The range a splitter may actually offer, at THIS column height.
 *
 * 20/80 alone is a promise the layout cannot keep.  Both regions have
 * floors measured in PIXELS — a map below ~120px is not a map, and the
 * list below it owes room to its header and one row — while a splitter
 * speaks per cent.  On a 600px column 20% is 120px, so a map floor of
 * 220 silently refused the minimum the separator advertised, and the
 * deficit was taken out of the list, whose last rows fell off the panel
 * with nothing to scroll them back.  Home, End, the drag and
 * `aria-valuemin`/`max` now all quote the same reachable numbers.
 *
 * Too short to pay both floors: the region BELOW wins.  The map just
 * gets small, which still works; the list losing its header and its
 * first row leaves no way to pick another vehicle at all.
 */
export function rangeFor(height: number, abovePx?: number, belowPx?: number): { lo: number; hi: number } {
  let lo = MIN_PCT, hi = MAX_PCT;
  if (height > 0) {
    if (abovePx != null) lo = Math.max(lo, (abovePx / height) * 100);
    if (belowPx != null) hi = Math.min(hi, ((height - belowPx) / height) * 100);
  }
  lo = Math.min(100, Math.max(0, Math.round(lo)));
  hi = Math.min(100, Math.max(0, Math.round(hi)));
  return lo > hi ? { lo: hi, hi } : { lo, hi };
}
