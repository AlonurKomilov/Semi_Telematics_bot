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
import { ROUTE_ENTRIES } from '../components/shell/routeRegistry';

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

/**
 * What each place costs to reach.
 *
 * Taken from `ROUTE_ENTRIES` rather than written again here: the
 * registry already answers "who may open this page", and a second copy
 * of that answer is a copy that will disagree. A surface whose route
 * the registry does not carry gets no permission and is therefore never
 * offered — fail closed, and `surfaces.test.ts` makes it loud.
 */
export function permissionFor(surface: Surface): readonly string[] | null {
  const entry = ROUTE_ENTRIES.find((r) => r.path === surface.route);
  if (!entry) return null;
  if (entry.permission === null) return [];
  return Array.isArray(entry.permission) ? entry.permission : [entry.permission];
}

/**
 * The places THIS person may aim the picker at.
 *
 * VIEW is the gate, not manage — the decision behind "per feature
 * selections", taken 2026-09-06.
 *
 * A background is not access. Choosing how Loads reads on your own
 * screen changes nothing for anyone else: the preference is per-user
 * and per-device, so there is no "manage" dimension for it to require.
 * What permission genuinely decides is whether the place should be
 * NAMED at all — offering "Work Orders" to somebody who cannot open
 * Work Orders both leaks that it exists and hands them a setting for a
 * screen they will never see.
 *
 * And requiring manage would have removed the surface the feature was
 * built for: Live Map has no `can_manage_*` verb anywhere in the
 * taxonomy — it is view-only by design — so a manage gate would make
 * the wall display, the screen read from across a room, un-themeable by
 * everybody including the owner.
 *
 * `ready` is not a permission. Until the active view's permissions have
 * settled, `hasAny` answers false for everything, and an unknown is not
 * a denial — so the picker offers nothing and says nothing rather than
 * showing a list that grows a second later.
 */
export function selectableSurfaces(
  hasAny: (...flags: string[]) => boolean,
  ready = true,
  /** The list to filter. Defaults to the shipped one; a test passes its
   *  own so the fail-closed branch and the any-of rule are reachable —
   *  today's three surfaces each name exactly one permission and each
   *  name a route the registry carries, so neither branch could be
   *  exercised by the real list, and a guard that cannot reach a branch
   *  is not guarding it. */
  from: readonly Surface[] = SURFACES,
): readonly Surface[] {
  if (!ready) return [];
  return from.filter((s) => {
    const perms = permissionFor(s);
    if (perms === null) return false;
    return perms.length === 0 || hasAny(...perms);
  });
}

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
