/**
 * Shell registry — picks the shell component for the active persona.
 * Each entry is keyed by the role string used in the JWT and by
 * RoleViewContext's ``activeView``.
 *
 * Roles without an entry, and unknown values, fall back to
 * DefaultShell via :func:`pickShell` — so a role added on the backend
 * before anyone builds a shell for it still renders a dashboard.
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

// Every persona renders the same chrome (AppShell) and the same page
// content. The ONE thing that varies today is the topbar hero, which
// each shell file names. Editing FleetShell.tsx is how a Fleet-only
// change lands without touching the other five — that seam is why the
// files exist separately, now that none of the duplication does.
//
// The sidebar is NOT part of it: no shell has ever passed it a nav
// config. It derives its own from the active persona.
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
