/**
 * Persona-aware presentational config for the Overview page.
 *
 * Lives at the FEATURE level (not inside a section) because the
 * Pattern B contract requires sections to be persona-agnostic:
 * sections receive resolved values via sectionProps and never read
 * persona from context.  The Overview page wrapper (Overview.tsx)
 * looks up persona once, resolves these tables into concrete values,
 * and hands the result to PageLayoutHost as part of sectionProps.
 *
 * Adding a new persona means:
 *   1. Add it to ``Persona`` in features/_lib/types.ts
 *   2. Add an entry here (HeaderConfig + KpiKey priority)
 *   3. Add an entry in layouts.ts
 *   4. (Optional) Add backend permission defaults
 */
import type { Persona } from '../_lib/types';

export interface HeaderConfig {
  title: string;
  description: string;
}

export type KpiKey =
  | 'pendingAlerts'
  | 'lowFuel'
  | 'faults'
  | 'maintenance'
  | 'unsafeParking'
  | 'aiBriefing'
  | 'vehiclesLink'
  | 'routesLink';

// Persona → page header copy.  Owner / Admin share "Operations
// Overview" because they manage the account holistically; Fleet /
// Dispatch / Safety each get their own slant.  HR / Accounting fall
// back to owner copy via the resolver below.
const ROLE_TITLES: Partial<Record<Persona, HeaderConfig>> = {
  owner: {
    title: 'Operations Overview',
    description:
      'Account-wide health — vehicle status, alerts, costs, and team activity. Tap any card to drill in.',
  },
  admin: {
    title: 'Operations Overview',
    description:
      'Account-wide health — vehicle status, alerts, costs, and team activity. Tap any card to drill in.',
  },
  fleet: {
    title: 'Fleet Overview',
    description:
      'At-a-glance health of your fleet — alerts that need action, current vehicle status, and quick drill-downs.',
  },
  dispatcher: {
    title: 'Dispatch Overview',
    description:
      "Vehicle movement, active routes, and alerts that need triage. Use this page to see who's rolling and where attention is needed.",
  },
  safety: {
    title: 'Safety Overview',
    description:
      'Pending alerts, unsafe events, parking issues, and the trucks that need follow-up. Drill into scorecards or coaching from any card.',
  },
};

// Per-persona drill-down KPI priority — earlier keys render first.
//
// Strict role binding: each persona's array only includes KPIs that
// match THAT persona's workspace.  An Owner viewing as Owner sees
// executive signals (pendingAlerts → owner_admin-scoped, AI briefing,
// nav links) — they do NOT see Fleet's faults card or Dispatch's
// fuel card.  To do operational triage Owner switches view →
// matching persona's array applies → operational cards appear.
//
// Mirrors capabilities.alerting.persona_mapping on the backend:
//   • dispatcher → live-ops cards (parking, fuel, alerts)
//   • safety     → behavior cards (alerts, vehicles link)
//   • fleet      → mechanical cards (faults, maintenance, alerts)
//   • hr         → workforce-adjacent cards (alerts)
//   • accounting → executive financial signals only
//   • owner/admin → cross-cutting digest + nav links
const ROLE_KPI_PRIORITY: Partial<Record<Persona, ReadonlyArray<KpiKey>>> = {
  owner: [
    'pendingAlerts',
    'aiBriefing', 'vehiclesLink', 'routesLink',
  ],
  admin: [
    'pendingAlerts',
    'aiBriefing', 'vehiclesLink', 'routesLink',
  ],
  fleet: [
    'faults', 'maintenance',
    'pendingAlerts',
    'aiBriefing', 'vehiclesLink', 'routesLink',
  ],
  dispatcher: [
    'pendingAlerts', 'unsafeParking', 'lowFuel',
    'routesLink', 'vehiclesLink', 'aiBriefing',
  ],
  safety: [
    'pendingAlerts',
    'aiBriefing', 'vehiclesLink',
  ],
  hr: [
    'pendingAlerts',
    'vehiclesLink', 'aiBriefing',
  ],
  accounting: [
    'aiBriefing', 'vehiclesLink',
  ],
  driver: [
    'pendingAlerts',
  ],
};

// Last-resort defaults if even the owner entry is somehow missing.
// The ``Partial<Record<Persona, T>>`` type means the compiler can't
// guarantee owner is present, so a runtime fallback prevents a hard
// crash if a future refactor accidentally drops the entry.  These
// defaults render as a generic but functional Overview.
const FALLBACK_HEADER: HeaderConfig = {
  title: 'Overview',
  description: 'Operational summary for your account.',
};
const FALLBACK_KPI_PRIORITY: ReadonlyArray<KpiKey> = [
  'pendingAlerts', 'faults', 'lowFuel', 'aiBriefing',
];

/**
 * Resolve the header config for the active persona.  Used by the
 * Overview page wrapper to compute sectionProps; sections themselves
 * never call this.  Falls through persona → owner → admin →
 * FALLBACK_HEADER so a missing entry never crashes the page.
 */
export function resolveHeaderConfig(persona: Persona): HeaderConfig {
  return (
    ROLE_TITLES[persona] ??
    ROLE_TITLES.owner ??
    ROLE_TITLES.admin ??
    FALLBACK_HEADER
  );
}

/**
 * Resolve the drill-down KPI priority list for the active persona.
 * Same fallback chain as resolveHeaderConfig.
 */
export function resolveKpiPriority(persona: Persona): ReadonlyArray<KpiKey> {
  return (
    ROLE_KPI_PRIORITY[persona] ??
    ROLE_KPI_PRIORITY.owner ??
    ROLE_KPI_PRIORITY.admin ??
    FALLBACK_KPI_PRIORITY
  );
}
