/**
 * The places that can wear a look of their own.
 *
 * A FIXED list, not a route pattern, and the difference is what can be
 * checked. GX matches URL patterns because it styles pages it did not
 * author — the web cannot be enumerated. Ours can: forty-one routes,
 * all of them ours. A pattern here would buy nothing and cost the two
 * things a list gives — a typo that matches nothing fails loudly
 * instead of silently, and the control is a named button rather than a
 * text field somebody types a glob into.
 *
 * Three to start, and deliberately not all forty-one. Each is here
 * because the SCREEN it lives on has a different viewing condition, not
 * because the page is important: the wall display is read from across
 * a room, the cab tablet is read in sunlight, the dispatcher's board is
 * read at arm's length all day. Every other route wears the global look,
 * and that is a stated answer rather than an accident.
 *
 * What a surface may change is the CANVAS — and through it every
 * surface colour derived from it. It may not change the accent. The
 * accent is the brand; varying it per page would make one product feel
 * like several, which is the opposite of why mods exist here. The
 * viewing condition varies by screen; the identity does not.
 */
export interface Surface {
  /** Stored key, and the value stamped as `data-surface`. */
  readonly id: string;
  /** What a person reads in the picker. */
  readonly title: string;
  /** The route this covers. A child path is covered too — `/loads/42`
   *  is the Loads board with one row open, not a different screen. */
  readonly route: string;
  /** Why this screen earns its own look, in the panel's own words. */
  readonly why: string;
}

export const SURFACES: readonly Surface[] = [
  { id: 'live-map',    title: 'Live Map',    route: '/live-map',
    why: 'read from across a room' },
  { id: 'loads',       title: 'Loads',       route: '/loads',
    why: 'read at arm’s length all day' },
  { id: 'work-orders', title: 'Work Orders', route: '/work-orders',
    why: 'read in a cab, in sunlight' },
];

export const surfaceById = (id: string): Surface | undefined =>
  SURFACES.find((s) => s.id === id);

/**
 * Which surface a path is, or null for everywhere else.
 *
 * Longest route wins, so a more specific surface added later beats a
 * general one however the list happens to be ordered — the same rule
 * `resolveAmbientView` follows, for the same reason.
 */
export function surfaceFor(path: string): Surface | null {
  let best: Surface | null = null;
  for (const s of SURFACES) {
    if (path !== s.route && !path.startsWith(`${s.route}/`)) continue;
    if (!best || s.route.length > best.route.length) best = s;
  }
  return best;
}
