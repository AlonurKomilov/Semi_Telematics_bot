/**
 * Unit width — Team Management's answer, read from ``/me``.
 *
 * Permissions answer VERBS (``can_view_vehicles``); width — "every
 * unit" vs "the trucks assigned to me" — is Team Management's, and
 * arrives as ``user.vehicle_scope``.  A surface the backend guards
 * with ``require_wide`` (utilisation heatmap, cross-company overlays,
 * the /vehicles link on a KPI tile) asks BOTH questions through
 * ``hasWide``; the nav's cross-department rule asks the same.
 *
 * ``undefined`` (not loaded yet) counts as wide: these gates hide
 * affordances, they are not boundaries — every route and API gates on
 * its own, and narrowing on a missing value would hide granted
 * features from a wide member during the first paint.
 */
export type VehicleScope = 'all' | 'assigned';

export function isWideScope(scope: VehicleScope | null | undefined): boolean {
  return scope !== 'assigned';
}

/** ``has(...flags)`` AND the member is wide. */
export function hasWideScope(
  has: (...flags: string[]) => boolean,
  scope: VehicleScope | null | undefined,
  ...flags: string[]
): boolean {
  return isWideScope(scope) && has(...flags);
}

/** Person width — for the person-subject features ("my loads", "my
 *  paystubs", "my coaching", "my documents").  A pure function of the
 *  ROLE, mirroring capabilities/permissions/scope.person_width: a
 *  driver reads their own rows, everyone else the account's.  No
 *  storage, no Team Management control — "self" for anyone without a
 *  driver row is nonsense, and a driver who reads everyone's paystubs
 *  is a role change, not a width. */
export type PersonWidth = 'self' | 'all';

export function personWidthOf(role: string | undefined): PersonWidth {
  return role === 'driver' ? 'self' : 'all';
}
