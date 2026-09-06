/**
 * The verb grid is DERIVED — these tests pin that derivation against
 * the row model so the two lenses can never drift apart.
 */
import { describe, expect, it } from 'vitest';
import { FEATURE_CATALOG } from '../../config/featureCatalog';
import { DRIVER_PANEL_FLAGS, PERM_GROUPS, isHeader } from './permRows';
import { buildVerbGrid, driverBands, placedRows, serviceRows } from './verbGrid';

const grid = buildVerbGrid();

describe('verb grid completeness', () => {
  it('every tickable row appears exactly ONCE in the grid', () => {
    const tickable = PERM_GROUPS.flatMap((g) => g.flags).filter((f) => !isHeader(f));
    const placed = placedRows(grid);
    expect(placed.length).toBe(tickable.length);
    expect(new Set(placed).size).toBe(placed.length);
    for (const f of tickable) expect(placed, (f as { label: string }).label).toContain(f);
  });

  it('the cross-feature band is exactly the config family', () => {
    expect(grid.crossFeature.map((c) => ('key' in c ? c.key : '')).sort())
      .toEqual(['can_manage_config_all', 'can_manage_config_role']);
  });

  it('bare "Manage" children are promoted into their parent row', () => {
    for (const b of grid.bands) for (const fam of b.families) {
      for (const c of fam.children) expect(c.row.label).not.toBe('Manage');
      if (fam.manage) expect(fam.manage.label).toBe('Manage');
    }
  });

  it('config cells only ride known family flags on known features', () => {
    // Storage and Integrations joined when account_settings ownership
    // moved off their Manage actions and onto the config family — the
    // matrix had been showing "–" in Config for settings that existed.
    const withCfg = grid.bands.flatMap((b) => b.families).filter((f) => f.configVia);
    expect(withCfg.map((f) => ('allKey' in f.parent ? f.parent.allKey : (f.parent as { key: string }).key)).sort())
      .toEqual([
        'can_manage_account',
        'can_manage_applications',
        'can_manage_storage',
        'can_view_kpi',
        'can_view_scorecards',
        // Vehicle source policy (precedence + auto-pilot).  Moved off
        // can_manage_integrations when its gear moved to the Vehicles
        // page — the tick follows the surface the grant opens.
        'can_view_vehicles',
      ]);
  });

  it('merged families are single write-level flags, never scoped pairs', () => {
    for (const b of grid.bands) for (const fam of b.families) {
      if (fam.merged) expect('allKey' in fam.parent).toBe(false);
    }
  });
});

describe('the Driver tab', () => {
  it('covers every driver flag exactly once — nothing strands', () => {
    // The driver's flags live outside the staff row model, so the
    // completeness guarantee has to be stated separately: the tab's two
    // bands ARE the driver panel's flag list, or a grant becomes
    // unreachable in the lens.
    const banded = driverBands().flatMap((b) => b.rows);
    expect(banded.length).toBe(DRIVER_PANEL_FLAGS.length);
    expect(new Set(banded).size).toBe(banded.length);
    for (const f of DRIVER_PANEL_FLAGS) expect(banded).toContain(f);
  });

  it('driver rows never appear in the staff verb grid', () => {
    const staff = new Set(placedRows(buildVerbGrid()));
    for (const f of DRIVER_PANEL_FLAGS) {
      // The driver's rows are their OWN objects even where the key is
      // the staff matrix's verb (can_view_loads read at self width).
      expect(staff.has(f), (f as { label: string }).label).toBe(false);
    }
  });
});

describe('the Services band', () => {
  it('is the catalog\u2019s services — never a second hand-written list', () => {
    const fromCatalog = FEATURE_CATALOG.filter((e) => e.kind === 'service').map((e) => e.id).sort();
    expect(serviceRows().map((s) => s.id).sort()).toEqual(fromCatalog);
  });

  it('every service carries copy — a blank row would explain nothing', () => {
    for (const s of serviceRows()) {
      expect(s.label, s.id).toBeTruthy();
      expect(s.note.length, s.id).toBeGreaterThan(20);
    }
  });

  it('no service is also a grantable row', () => {
    // Services are derived (derive_service_perms) and the save endpoint
    // strips their flags; a tickable row would be a lying checkbox.
    const grantable = new Set(placedRows(buildVerbGrid()).map((f) =>
      ('allKey' in f ? f.allKey : (f as { key?: string }).key)));
    for (const id of serviceRows().map((s) => s.id)) {
      expect(grantable.has(`can_${id}`), id).toBe(false);
    }
  });
});


describe('services can own config', () => {
  it('Alerts declares its Group delivery config', () => {
    // A service row renders "always on, nothing to grant". That was true
    // when Alerts was only an inbox; Group delivery writes account_settings
    // behind can_manage_config_all, so the Config column had to stop
    // saying "-" for a grant that really exists.
    const alerts = serviceRows().find((s) => s.id === 'alerts');
    expect(alerts?.configVia).toBe('can_manage_config_all');
  });

  it('services WITHOUT config still declare none', () => {
    // Guards the opposite error: a blanket tick on every service would
    // promise grants that do not exist for AI Assistant and Reports.
    for (const id of ['ai_assistant', 'reports']) {
      expect(serviceRows().find((s) => s.id === id)?.configVia).toBeUndefined();
    }
  });
});
