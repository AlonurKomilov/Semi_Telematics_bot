/**
 * The verb grid is DERIVED — these tests pin that derivation against
 * the row model so the two lenses can never drift apart.
 */
import { describe, expect, it } from 'vitest';
import { PERM_GROUPS, isHeader } from './permRows';
import { buildVerbGrid, placedRows } from './verbGrid';

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
