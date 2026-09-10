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
    // The Inspections PAGE is the management console (2026-09-10: its sign
    // names the manage verb; the view verb opens the bot / mini-app).
    const nav = paths(generateNav('safety', grants(
      'can_view_maintenance', 'can_view_work_orders', 'can_manage_inspections',
      'can_view_events', 'can_view_scorecards', 'can_view_location', 'can_view_vehicles',
    ), undefined));
    expect(nav).toContain('/maintenance');
    expect(nav).toContain('/work-orders');
    expect(nav).toContain('/inspections');
  });

  it('does NOT surface cross-department features on own-scope baseline crumbs (Recruiter stays clean)', () => {
    // Recruiter's driver-equivalent baseline is all _vehicle-scoped — none of
    // the fleet-module features may leak into the recruiting dashboard.
    // In the verb grammar the crumbs are plain view verbs; what keeps
    // them off the recruiting dashboard is the member's WIDTH — Team
    // Management's 'assigned', passed from /me — not a flag suffix.
    const nav = paths(generateNav('recruiter', grants(
      'can_view_maintenance', 'can_view_work_orders', 'can_view_inspections',
      'can_view_routes', 'can_view_events', 'can_view_scorecards',
      'can_view_location', 'can_view_vehicles', 'can_manage_applications',
    ), undefined, 'assigned'));
    expect(nav).not.toContain('/maintenance');
    expect(nav).not.toContain('/work-orders');
    expect(nav).not.toContain('/inspections');
    expect(nav).not.toContain('/routes');
  });

  it('own-department features still honour permissions (no grant → no item)', () => {
    const nav = paths(generateNav('fleet', grants('can_view_vehicles'), undefined));
    expect(nav).not.toContain('/maintenance');   // fleet module, but flag not held
    expect(nav).toContain('/vehicles');
  });

  it('Owner/Admin keep the curated account nav — cross-grants do NOT explode it', () => {
    // Owner holds everything account-wide; the account-persona sidebar must
    // still exclude department-module tools (they persona-switch for ops).
    const nav = paths(generateNav('owner', grants(
      'can_view_maintenance', 'can_view_work_orders', 'can_view_routes',
      'can_manage_users', 'can_manage_account', 'can_manage_permissions',
      'can_view_vehicles', 'can_view_location',
    ), undefined));
    expect(nav).not.toContain('/maintenance');
    expect(nav).not.toContain('/routes');
    expect(nav).toContain('/settings');
  });

  it('a disabled account module still hides the feature (module mask wins)', () => {
    const nav = paths(generateNav('safety', grants('can_view_maintenance', 'can_view_vehicles'),
      ['safety', 'hr']));   // fleet module disabled account-wide
    expect(nav).not.toContain('/maintenance');
  });
});

describe('generateNav — item-level children (Settings-style nesting)', () => {
  it('a child folds under its parent, not as a flat sibling', () => {
    // Documents is the example since Onboard Inventory left Vehicles
    // (2026-09-08) and became a feature of its own; the FOLDING rule
    // this pins is unchanged, only which entry demonstrates it.
    const nav = generateNav('fleet', grants(
      'can_view_vehicles', 'can_view_vehicle_docs', 'can_view_location', 'can_view_maintenance',
    ), undefined);
    const flat = nav.flatMap((g) => g.items);
    const vehicles = flat.find((i) => i.path === '/vehicles');
    expect(vehicles).toBeDefined();
    expect(vehicles?.children?.map((c) => c.path)).toContain('/vehicles/documents');
    // …and it is NOT duplicated as a top-level entry
    expect(flat.map((i) => i.path)).not.toContain('/vehicles/documents');
  });

  it('Inventory stands on its own, not under Vehicles', () => {
    const nav = generateNav('fleet', grants(
      'can_view_vehicles', 'can_view_inventory', 'can_view_location',
    ), undefined);
    const flat = nav.flatMap((g) => g.items);
    expect(flat.map((i) => i.path)).toContain('/inventory');
    const vehicles = flat.find((i) => i.path === '/vehicles');
    expect(vehicles?.children?.map((c) => c.path) ?? []).not.toContain('/inventory');
  });

  it('its grant alone is enough — Vehicles is not a prerequisite', () => {
    // The point of the split: a person may read what is in a truck
    // without being able to administer the registry.
    const flat = generateNav('safety', grants('can_view_inventory'), undefined)
      .flatMap((g) => g.items);
    expect(flat.map((i) => i.path)).toContain('/inventory');
  });

  it('an orphaned child falls back to a flat entry (grant never unreachable)', () => {
    // A granted child must never be lost: it appears EITHER nested under
    // its parent or flat, never nowhere.
    const nav = generateNav('owner', grants(
      'can_view_vehicles', 'can_view_vehicle_docs', 'can_view_location',
    ), undefined);
    const all = nav.flatMap((g) => g.items.flatMap((i) => [i, ...(i.children ?? [])]));
    expect(all.map((i) => i.path)).toContain('/vehicles/documents');
  });
});

describe('generateNav — role manager reaches Settings (parent-only group)', () => {
  it('a fleet manager with can_manage_role_bot gets the settings group with /settings as parentItem', () => {
    const nav = generateNav('fleet', grants('can_manage_role_bot', 'can_view_vehicles'), undefined);
    const settingsGroup = nav.find((g) => g.collapsible);
    // The group must exist and carry the parent even with ZERO children —
    // the Sidebar renders parentItem; dropping the group hid Settings
    // from every role manager (live bug 2026-07-22).
    expect(settingsGroup).toBeDefined();
    expect(settingsGroup?.parentItem?.path).toBe('/settings');
    expect(settingsGroup?.items).toHaveLength(0);
  });

  it('an employee without the flag gets no settings group at all', () => {
    const nav = generateNav('fleet', grants('can_view_vehicles'), undefined);
    expect(nav.find((g) => g.collapsible)).toBeUndefined();
  });

  it('a person view verb surfaces cross-department at person width all — never for a driver', () => {
    // A driver holds the coaching / driver-pay verbs at SELF width (the
    // role's): the staff pages must not appear in their sidebar.  A
    // dispatcher holding the same verbs reads the account.  Coaching and
    // Driver Pay PAGES are management consoles (2026-09-10): their signs
    // name the manage verb; Drivers opens on the docs' view verb.
    const held = grants('can_manage_coaching', 'can_manage_driver_pay', 'can_view_driver_docs', 'can_view_vehicles');
    const driver = paths(generateNav('driver', held, undefined, 'assigned'));
    expect(driver).not.toContain('/coaching');
    expect(driver).not.toContain('/driver-pay');
    expect(driver).not.toContain('/workforce/drivers');
    const dispatcher = paths(generateNav('dispatcher', held, undefined));
    expect(dispatcher).toContain('/coaching');
    expect(dispatcher).toContain('/driver-pay');
    expect(dispatcher).toContain('/workforce/drivers');
  });
});
