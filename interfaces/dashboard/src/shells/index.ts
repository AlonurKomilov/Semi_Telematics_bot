/**
 * Shell registry — picks the right shell component for the active
 * persona.  Each entry is keyed by the role string used in the JWT and
 * the RoleViewContext's ``activeView``.
 *
 * Phase 0 of the role-shell migration has every role mapped to
 * DefaultShell so the UI is identical to the pre-refactor Layout.
 * Subsequent phases will add FleetShell, DispatchShell, SafetyShell
 * etc. — at which point each role's mapping flips to its tuned shell.
 *
 * Roles without an entry (or unknown values) fall back to
 * DefaultShell via :func:`pickShell`.  This keeps the system safe
 * against new roles being added on the backend before a shell is
 * built for them.
 */
import { type ComponentType } from 'react';
import DefaultShell from './DefaultShell';
import FleetShell from './FleetShell';
import DispatchShell from './DispatchShell';
import SafetyShell from './SafetyShell';

export type ShellKey = 'owner' | 'admin' | 'fleet' | 'dispatcher' | 'safety' | 'driver';

// Phase 2 of the role-shell migration: each non-Owner/Admin role now
// renders through its dedicated shell file.  At this point the shells
// are clones of DefaultShell, so behavior is identical to Phase 0/1.
// Phase 3 swaps each shell's <Sidebar /> for a navConfig prop tuned to
// that persona; Phase 4 layers in per-role hero widgets.  Until then,
// editing FleetShell.tsx / DispatchShell.tsx / SafetyShell.tsx is the
// way to introduce per-role behavior without touching the others.
export const SHELLS: Partial<Record<ShellKey, ComponentType>> = {
  owner:      DefaultShell,
  admin:      DefaultShell,
  fleet:      FleetShell,
  dispatcher: DispatchShell,
  safety:     SafetyShell,
  driver:     DefaultShell,   // Drivers use the Mini App; this is a fallback only
};

/** Resolve a shell component for the active persona.  Unknown roles
 *  fall back to DefaultShell so the dashboard never has a missing
 *  layout. */
export function pickShell(activeView: string): ComponentType {
  return SHELLS[activeView as ShellKey] ?? DefaultShell;
}
