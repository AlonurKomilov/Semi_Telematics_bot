import { describe, it, expect } from 'vitest';
import { generateNav } from './generateNav';

/** Build a has() from a granted-flag set (mirrors viewHasAny). */
const grants = (...flags: string[]) => {
  const s = new Set(flags);
  return (...ks: string[]) => ks.some((k) => s.has(k));
};

const paths = (groups: ReturnType<typeof generateNav>) =>
  groups.flatMap((g) => [...(g.parentItem ? [g.parentItem] : []), ...g.items]).map((i) => i.path);

describe('generateNav — the matrix is the source of truth for the sidebar', () => {
  it('surfaces cross-department features on an ACCOUNT-WIDE grant (Safety → fleet tools)', () => {
    // Safety's real defaults include account-wide maintenance / work-orders /
    // inspections — those fleet-module features must appear in its sidebar.
    const nav = paths(generateNav('safety', grants(
      'can_maintenance_all', 'can_work_orders_all', 'can_inspections_all',
      'can_events_all', 'can_scorecard_all', 'can_location_map', 'can_vehicle_all',
    ), undefined));
    expect(nav).toContain('/maintenance');
    expect(nav).toContain('/work-orders');
    expect(nav).toContain('/inspections');
  });

  it('does NOT surface cross-department features on own-scope baseline crumbs (Recruiter stays clean)', () => {
    // Recruiter's driver-equivalent baseline is all _vehicle-scoped — none of
    // the fleet-module features may leak into the recruiting dashboard.
    const nav = paths(generateNav('recruiter', grants(
      'can_maintenance_vehicle', 'can_work_orders_vehicle', 'can_inspections_vehicle',
      'can_route_vehicle', 'can_events_vehicle', 'can_scorecard_vehicle',
      'can_location_vehicle', 'can_vehicle_vehicle', 'can_manage_applications',
    ), undefined));
    expect(nav).not.toContain('/maintenance');
    expect(nav).not.toContain('/work-orders');
    expect(nav).not.toContain('/inspections');
    expect(nav).not.toContain('/routes');
  });

  it('own-department features still honour permissions (no grant → no item)', () => {
    const nav = paths(generateNav('fleet', grants('can_vehicle_all'), undefined));
    expect(nav).not.toContain('/maintenance');   // fleet module, but flag not held
    expect(nav).toContain('/vehicles');
  });

  it('Owner/Admin keep the curated account nav — cross-grants do NOT explode it', () => {
    // Owner holds everything account-wide; the account-persona sidebar must
    // still exclude department-module tools (they persona-switch for ops).
    const nav = paths(generateNav('owner', grants(
      'can_maintenance_all', 'can_work_orders_all', 'can_route_all',
      'can_manage_users', 'can_manage_account', 'can_manage_permissions',
      'can_vehicle_all', 'can_location_map',
    ), undefined));
    expect(nav).not.toContain('/maintenance');
    expect(nav).not.toContain('/routes');
    expect(nav).toContain('/settings');
  });

  it('a disabled account module still hides the feature (module mask wins)', () => {
    const nav = paths(generateNav('safety', grants('can_maintenance_all', 'can_vehicle_all'),
      ['safety', 'hr']));   // fleet module disabled account-wide
    expect(nav).not.toContain('/maintenance');
  });
});
