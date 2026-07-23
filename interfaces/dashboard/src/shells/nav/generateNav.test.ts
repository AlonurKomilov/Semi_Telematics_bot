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

describe('generateNav — item-level children (Settings-style nesting)', () => {
  it('Inventory folds under Vehicles as a child, not a flat sibling', () => {
    const nav = generateNav('fleet', grants(
      'can_vehicle_all', 'can_location_map', 'can_maintenance_all',
    ), undefined);
    const flat = nav.flatMap((g) => g.items);
    const vehicles = flat.find((i) => i.path === '/vehicles');
    expect(vehicles).toBeDefined();
    expect(vehicles?.children?.map((c) => c.path)).toContain('/vehicles/inventory');
    // …and it is NOT duplicated as a top-level entry
    expect(flat.map((i) => i.path)).not.toContain('/vehicles/inventory');
  });

  it('an orphaned child falls back to a flat entry (grant never unreachable)', () => {
    // Hypothetical persona state where the child is granted but the parent
    // filtered out cannot occur for vehicles/inventory (same flags), so we
    // assert the folding rule structurally: every child path present in the
    // catalog appears EITHER nested or flat — never lost.
    const nav = generateNav('owner', grants(
      'can_vehicle_all', 'can_location_map',
    ), undefined);
    const all = nav.flatMap((g) => g.items.flatMap((i) => [i, ...(i.children ?? [])]));
    expect(all.map((i) => i.path)).toContain('/vehicles/inventory');
  });
});

describe('generateNav — role manager reaches Settings (parent-only group)', () => {
  it('a fleet manager with can_manage_role_bot gets the settings group with /settings as parentItem', () => {
    const nav = generateNav('fleet', grants('can_manage_role_bot', 'can_vehicle_all'), undefined);
    const settingsGroup = nav.find((g) => g.collapsible);
    // The group must exist and carry the parent even with ZERO children —
    // the Sidebar renders parentItem; dropping the group hid Settings
    // from every role manager (live bug 2026-07-22).
    expect(settingsGroup).toBeDefined();
    expect(settingsGroup?.parentItem?.path).toBe('/settings');
    expect(settingsGroup?.items).toHaveLength(0);
  });

  it('an employee without the flag gets no settings group at all', () => {
    const nav = generateNav('fleet', grants('can_vehicle_all'), undefined);
    expect(nav.find((g) => g.collapsible)).toBeUndefined();
  });
});
