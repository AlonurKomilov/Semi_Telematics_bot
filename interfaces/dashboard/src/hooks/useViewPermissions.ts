import { useCallback } from 'react';
import { useRoleView } from '../context/RoleViewContext';
import { useAuth } from '../context/AuthContext';
import { hasWideScope, type VehicleScope } from '../config/unitWidth';

interface UseViewPermissionsReturn {
  has: (flag: string) => boolean;
  hasAny: (...flags: string[]) => boolean;
  /** ``hasAny(...flags)`` AND the member's unit width is 'all' — for
   * surfaces the backend guards with ``require_wide``.  Width is Team
   * Management's answer (``/me`` ``vehicle_scope``), not a flag. */
  hasWide: (...flags: string[]) => boolean;
  /** The member's unit width from ``/me``; undefined until loaded. */
  vehicleScope: VehicleScope | undefined;
  role: string | undefined;
  /** See ``viewPermsReady`` on RoleViewContext.  Hiding UI on a false
   * ``has`` is fine without it; REDIRECTING on one is not. */
  ready: boolean;
}

/**
 * Persona-aware permission hook — a drop-in replacement for
 * ``usePermissions`` for any UI gating that should follow the ACTIVE
 * VIEW rather than the logged-in user.
 *
 * ``has`` / ``hasAny`` resolve against the previewed persona's
 * permissions when an Owner/Admin has switched view, and fall back to
 * the real user's own permissions otherwise (so a regular user, or an
 * owner on their own view, behaves exactly as before).  This is what
 * makes "view dashboard as Fleet" a faithful preview — affordances the
 * previewed persona lacks are hidden, instead of staying visible
 * because the *viewer* happens to be an owner.
 *
 * ``role`` is the real logged-in role (unchanged from usePermissions)
 * for the few call sites that label "saved as <your role>".
 *
 * Backend enforcement is independent and unchanged — this only governs
 * what the UI shows.  Use the raw ``usePermissions`` only when you
 * deliberately need the logged-in user's own permission regardless of
 * the previewed persona.
 */
export function useViewPermissions(): UseViewPermissionsReturn {
  const { viewHas, viewHasAny, viewPermsReady } = useRoleView();
  const { user } = useAuth();
  const has = useCallback((flag: string) => viewHas(flag), [viewHas]);
  const hasAny = useCallback(
    (...flags: string[]) => viewHasAny(...flags), [viewHasAny],
  );
  const vehicleScope = user?.vehicle_scope;
  const hasWide = useCallback(
    (...flags: string[]) => hasWideScope(viewHasAny, vehicleScope, ...flags),
    [viewHasAny, vehicleScope],
  );
  return { has, hasAny, hasWide, vehicleScope, role: user?.role, ready: viewPermsReady };
}
