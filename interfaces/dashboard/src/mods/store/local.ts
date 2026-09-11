/**
 * Local — what this install actually has, and the one door every mods
 * surface asks.
 *
 * The store is the catalogue of everything that exists; local is the
 * part of it this copy of the app carries. Today that is all of it:
 * a CSS pack reaches the page through the `@import` chain in
 * `index.css`, an icon pack through `import()`, so every pack the store
 * lists is already here. Nothing is fetched, and this file does not
 * pretend otherwise — it is the READ MODEL of the store, not a
 * downloads folder. When a pack is one day delivered instead of built
 * in, or withheld because a plan does not include it, this is the one
 * place that answers differently, and every picker on every axis
 * changes with it.
 *
 * It is also a LEAF, on purpose: it imports the catalogue and types,
 * and NOTHING else. In particular it never reads preferences. What a
 * person CHOSE is a preference; what this install HAS is this file; the
 * one module allowed to hold both hands is `context.tsx`, the painter.
 * Wire it any other way and `preferences/registry.ts` — which asks the
 * store for its valid ids — closes a ring through this file, and a Vite
 * ESM cycle hands whichever module initialises first an `undefined`
 * list. That breaks as `.includes` of undefined during the boot
 * sanitise, in production only, in an order that changes per build.
 */
import { STORE, rowsOf, type StoreRow } from './index';

/** Everything this install carries — the whole catalogue, today. */
export const installed = (): readonly StoreRow[] => STORE;

/** What a picker on one axis may offer. */
export const installedIds = (axis: string): readonly string[] =>
  rowsOf(axis).filter((r) => has(r)).map((r) => r.id);

export const isInstalled = (axis: string, id: string): boolean =>
  installedIds(axis).includes(id);

/**
 * What a picker may draw — the door every mods surface goes through.
 *
 * Takes the axis's own typed list and hands back the part this install
 * carries, so a picker keeps its payload (a seed, a cue table, a
 * wallpaper's kind) and loses only the rows that are not here. The id
 * getter is explicit because an option table calls the id `value` and a
 * pack calls it `id`, and a helper that guesses would silently offer
 * everything the day one of them is renamed.
 *
 * Called at RENDER, never frozen into a module constant: the day this
 * answers differently per person, a constant computed at import time
 * would still be showing what the first paint saw.
 */
export const offered = <T,>(
  axis: string, items: readonly T[], idOf: (item: T) => string,
): readonly T[] => items.filter((i) => isInstalled(axis, idOf(i)));

/** The one test a row has to pass to be here. Its own function because
 *  it is the seam: a plan mask or a delivery check lands in this line,
 *  and nothing above it has to move. */
const has = (_row: StoreRow): boolean => true;
