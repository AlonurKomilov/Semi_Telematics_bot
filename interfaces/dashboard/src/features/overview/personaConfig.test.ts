/**
 * Locks the strict-binding KPI priorities — each persona's array
 * MUST only include KPIs that belong to that persona's workspace.
 * A regression here means an Owner view starts showing Fleet's
 * faults card (or worse) without anyone noticing in code review.
 */
import { describe, it, expect } from 'vitest';
import { resolveKpiPriority, type KpiKey } from './personaConfig';

// What each persona is ALLOWED to show.  Anything outside this set in
// the persona's priority array is a strict-binding violation.
const ALLOWED: Record<string, Set<KpiKey>> = {
  owner: new Set(['pendingAlerts', 'aiBriefing', 'vehiclesLink', 'routesLink']),
  admin: new Set(['pendingAlerts', 'aiBriefing', 'vehiclesLink', 'routesLink']),
  fleet: new Set([
    'faults', 'maintenance', 'pendingAlerts',
    'aiBriefing', 'vehiclesLink', 'routesLink',
  ]),
  dispatcher: new Set([
    'pendingAlerts', 'unsafeParking', 'lowFuel',
    'routesLink', 'vehiclesLink', 'aiBriefing',
  ]),
  safety: new Set(['pendingAlerts', 'aiBriefing', 'vehiclesLink']),
  hr: new Set(['pendingAlerts', 'vehiclesLink', 'aiBriefing']),
  accounting: new Set(['aiBriefing', 'vehiclesLink']),
  driver: new Set(['pendingAlerts']),
};

describe('overview personaConfig — strict role binding', () => {
  for (const [persona, allowed] of Object.entries(ALLOWED)) {
    it(`${persona} priority only contains ${persona}-relevant KPIs`, () => {
      const priority = resolveKpiPriority(persona as Parameters<typeof resolveKpiPriority>[0]);
      const leaked = priority.filter((k) => !allowed.has(k));
      expect(leaked).toEqual([]);
    });
  }

  it('owner does NOT show operational cards (faults / lowFuel / maintenance / unsafeParking)', () => {
    const owner = new Set(resolveKpiPriority('owner'));
    expect(owner.has('faults')).toBe(false);
    expect(owner.has('lowFuel')).toBe(false);
    expect(owner.has('maintenance')).toBe(false);
    expect(owner.has('unsafeParking')).toBe(false);
  });

  it('admin matches owner under strict binding (both are cross-cutting executive)', () => {
    expect([...resolveKpiPriority('admin')]).toEqual([...resolveKpiPriority('owner')]);
  });

  it('fleet does NOT show lowFuel or unsafeParking (those are dispatch)', () => {
    const fleet = new Set(resolveKpiPriority('fleet'));
    expect(fleet.has('lowFuel')).toBe(false);
    expect(fleet.has('unsafeParking')).toBe(false);
  });

  it('safety does NOT show faults or unsafeParking', () => {
    const safety = new Set(resolveKpiPriority('safety'));
    expect(safety.has('faults')).toBe(false);
    expect(safety.has('unsafeParking')).toBe(false);
    expect(safety.has('maintenance')).toBe(false);
  });

  it('dispatcher owns the live-ops triad', () => {
    const dispatch = new Set(resolveKpiPriority('dispatcher'));
    expect(dispatch.has('pendingAlerts')).toBe(true);
    expect(dispatch.has('unsafeParking')).toBe(true);
    expect(dispatch.has('lowFuel')).toBe(true);
    // Mechanical cards are NOT dispatch's job.
    expect(dispatch.has('faults')).toBe(false);
    expect(dispatch.has('maintenance')).toBe(false);
  });
});
