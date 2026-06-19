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
import HRShell from './HRShell';
import AccountingShell from './AccountingShell';

export type ShellKey =
  | 'owner'
  | 'admin'
  | 'fleet'
  | 'dispatcher'
  | 'safety'
  | 'driver'
  | 'hr'
  | 'accounting'
  | 'recruiter';

// Each non-Owner/Admin persona renders through its dedicated shell
// file.  Editing FleetShell.tsx / DispatchShell.tsx / SafetyShell.tsx
// / HRShell.tsx / AccountingShell.tsx is the way to introduce
// per-role behaviour without touching the others — shell chrome
// (sidebar nav + topbar hero) is the only persona-level difference;
// the page content is the same for every persona.
export const SHELLS: Partial<Record<ShellKey, ComponentType>> = {
  owner:      DefaultShell,
  admin:      DefaultShell,
  fleet:      FleetShell,
  dispatcher: DispatchShell,
  safety:     SafetyShell,
  hr:         HRShell,
  accounting: AccountingShell,
  // Recruiter ships on DefaultShell with a permission-generated minimal
  // nav (driver-equivalent default).  A dedicated RecruiterShell can be
  // added later for tuned chrome; the fallback keeps it safe meanwhile.
  recruiter:  DefaultShell,
  driver:     DefaultShell,   // Drivers use the Mini App; this is a fallback only
};

/** Resolve a shell component for the active persona.  Unknown roles
 *  fall back to DefaultShell so the dashboard never has a missing
 *  layout. */
export function pickShell(activeView: string): ComponentType {
  return SHELLS[activeView as ShellKey] ?? DefaultShell;
}
