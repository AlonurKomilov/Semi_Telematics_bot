/**
 * The verb grid is DERIVED — these tests pin that derivation against
 * the row model so the two lenses can never drift apart.
 */
import { describe, expect, it } from 'vitest';
import { DRIVER_PANEL_FLAGS, PERM_GROUPS, isHeader } from './permRows';
import { buildVerbGrid, driverBands, placedRows } from './verbGrid';

const grid = buildVerbGrid();

describe('verb grid completeness', () => {
  it('every tickable row appears exactly ONCE in the grid', () => {
    const tickable = PERM_GROUPS.flatMap((g) => g.flags).filter((f) => !isHeader(f));
    const placed = placedRows(grid);
    expect(placed.length).toBe(tickable.length);
    expect(new Set(placed).size).toBe(placed.length);
    for (const f of tickable) expect(placed, (f as { label: string }).label).toContain(f);
  });

  it('the capabilities band is exactly the config family', () => {
    expect(grid.capabilities.map((c) => ('key' in c ? c.key : '')).sort())
      .toEqual(['can_manage_config_all', 'can_manage_config_role']);
  });

  it('bare "Manage" children are promoted into their parent row', () => {
    for (const b of grid.bands) for (const fam of b.families) {
      for (const c of fam.children) expect(c.row.label).not.toBe('Manage');
      if (fam.manage) expect(fam.manage.label).toBe('Manage');
    }
  });

  it('config cells only ride known family flags on known features', () => {
    const withCfg = grid.bands.flatMap((b) => b.families).filter((f) => f.configVia);
    expect(withCfg.map((f) => ('allKey' in f.parent ? f.parent.allKey : (f.parent as { key: string }).key)).sort())
      .toEqual(['can_kpi', 'can_scorecard_all']);
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
      // can_loads_own / can_risk_report_own are the vehicleKey half of a
      // staff scoped PAIR, never a staff ROW of their own.
      expect(staff.has(f), (f as { label: string }).label).toBe(false);
    }
  });
});
