/**
 * Persona-aware Driver Scorecards page configuration.
 *
 * Scorecards is a Shared-tier feature (docs/FEATURES.md): the page is
 * one ranked table + fleet-level analysis blocks, so the persona
 * surface is WHICH PAGE BLOCKS render (the block flavor of persona
 * composition — same idea as the Drivers drawer tabs).
 *
 * The page wrapper resolves the active persona once (``useShellConfig``)
 * and gates blocks; block components never read persona state.  Data
 * scope (a driver seeing only their own card) is the backend's job —
 * this config only decides which ANALYSIS chrome is relevant per role.
 */
import type { Persona } from '../_lib/types';

export type ScorecardBlock = 'kpi' | 'distribution' | 'top_bottom' | 'table';

/** Owner/Admin superset — original page order. */
const ALL_BLOCKS: ScorecardBlock[] = ['kpi', 'distribution', 'top_bottom', 'table'];

/**
 * Per-persona page blocks.
 *
 *  - Safety/HR/Fleet do the coaching + utilization analysis → full page.
 *  - Dispatch only needs a quick "who's risky" lookup → KPI + table.
 *  - A driver sees their own card (backend-scoped) — fleet-distribution
 *    charts and fleet KPIs are noise for a population of one.
 *  - Accounting references scores (pay-for-performance) → KPI + table.
 */
export const BLOCKS_FOR_PERSONA: Record<Persona, ScorecardBlock[]> = {
  owner: ALL_BLOCKS,
  admin: ALL_BLOCKS,
  safety: ALL_BLOCKS,
  fleet: ALL_BLOCKS,
  hr: ALL_BLOCKS,
  dispatcher: ['kpi', 'table'],
  driver: ['table'],
  accounting: ['kpi', 'table'],
};

export function blocksForPersona(persona: Persona): ScorecardBlock[] {
  return BLOCKS_FOR_PERSONA[persona] ?? ALL_BLOCKS;
}
