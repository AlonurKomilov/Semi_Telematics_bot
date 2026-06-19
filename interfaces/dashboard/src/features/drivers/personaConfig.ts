/**
 * Persona-aware Drivers page configuration.
 *
 * Drivers is a Shared-tier feature (docs/FEATURES.md): visible to every
 * permitted role, but each persona works with different COMPONENTS of a
 * driver.  This page is a list + detail drawer, so the persona surface
 * is the drawer's TAB SET (the tab/column flavor of persona composition,
 * like the Vehicles list — not the vertical-section PageLayoutHost
 * flavor used by detail pages).
 *
 * The page wrapper resolves the active persona ONCE (via
 * ``useShellConfig``) and passes the resolved tab list down; the drawer
 * and tab bodies never read persona state themselves.
 *
 * Resolution mirrors ``usePersonaLayout``: persona → owner superset.
 */
import type { Persona } from '../_lib/types';

export type DriverDetailTab =
  | 'profile' | 'vehicles' | 'documents' | 'inspections' | 'trainings' | 'hos';

/** Owner/Admin superset — the original drawer order. */
const ALL_TABS: DriverDetailTab[] = [
  'profile', 'vehicles', 'documents', 'inspections', 'trainings', 'hos',
];

/**
 * Per-persona drawer tabs.
 *
 *  - HR owns the Drivers feature: people + compliance (docs, PTI history,
 *    trainings).  HOS is an ops/fatigue concern — omitted.
 *  - Fleet: who's on which truck, doc validity, PTI, HOS availability.
 *    Trainings (coaching) belongs to Safety/HR — omitted.
 *  - Safety: compliance + behavior (docs, PTI, trainings, HOS fatigue).
 *    Vehicle assignment isn't theirs to action — omitted.
 *  - Dispatch: "who can I put where, and for how long" — assignment + HOS.
 *  - Driver: own profile + own documents (the can_driver_docs_own case).
 *  - Accounting: identity only (pay lives in Payroll, not here).
 */
export const TABS_FOR_PERSONA: Record<Persona, DriverDetailTab[]> = {
  owner: ALL_TABS,
  admin: ALL_TABS,
  hr: ['profile', 'vehicles', 'documents', 'inspections', 'trainings'],
  fleet: ['profile', 'vehicles', 'documents', 'inspections', 'hos'],
  safety: ['profile', 'documents', 'inspections', 'trainings', 'hos'],
  dispatcher: ['profile', 'vehicles', 'hos'],
  driver: ['profile', 'documents'],
  accounting: ['profile'],
  // Recruiter: qualification-focused — CDL/medical docs, inspection +
  // training records for onboarding.  No operational tabs (vehicles/hos).
  recruiter: ['profile', 'documents', 'inspections', 'trainings'],
};

export function tabsForPersona(persona: Persona): DriverDetailTab[] {
  return TABS_FOR_PERSONA[persona] ?? ALL_TABS;
}

/** The fleet-wide expiring-documents banner only matters to personas
 *  that action documents (i.e. whose tab set includes Documents). */
export function showsExpiringBanner(persona: Persona): boolean {
  return tabsForPersona(persona).includes('documents');
}
