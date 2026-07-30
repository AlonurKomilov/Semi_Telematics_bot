/**
 * Drift guard for the Permissions matrix's TYPED row model.
 *
 * The matrix is the enforced mirror of docs/FEATURES.md "Structural
 * units": every row declares a kind, nesting follows the law
 * (sub-features may own children; capabilities never nest), and every
 * family lists its rows in one fixed order — so the taxonomy can't
 * silently rot back into implicit indentation.
 */
import { describe, expect, it } from 'vitest';
import { PERM_GROUPS } from './permRows';

type Row = {
  key?: string; allKey?: string; vehicleKey?: string; header?: string;
  label: string; kind?: string; parentKey?: string; writeLevel?: true;
  scoped?: boolean; indented?: boolean;
};

const KINDS = ['feature', 'subfeature', 'component', 'action', 'cross_feature'];
// Fixed family order: the parent itself, then Manage/actions, then
// sub-features, then components; capabilities close the (Settings) block.
const ORDER: Record<string, number> = {
  feature: 0, action: 1, subfeature: 2, component: 3, cross_feature: 4,
};

const allRows: Row[] = PERM_GROUPS.flatMap((g) => g.flags as Row[]);
const tickable = allRows.filter((r) => !('header' in r && r.header));
const primaryKey = (r: Row) => (r.allKey ?? r.key)!;

describe('every row declares its kind', () => {
  it('kind present and valid on all tickable rows', () => {
    for (const r of tickable) {
      expect(KINDS, `${r.label} has invalid kind ${r.kind}`).toContain(r.kind);
    }
  });

  it('scoped rows carry both keys', () => {
    for (const r of tickable.filter((x) => x.scoped)) {
      expect(r.allKey, r.label).toBeTruthy();
      expect(r.vehicleKey, r.label).toBeTruthy();
    }
  });

  it('the manage-tagged rows are exactly the known write-level set', () => {
    // The tag exists for noun-labeled rows whose single flag is
    // write-level.  Key NAMES can't prove that (can_service_tasks is
    // write-level with a neutral name), so the set is pinned explicitly —
    // tagging or untagging a row is a conscious edit here, never drift.
    const tagged = tickable.filter((x) => x.writeLevel).map(primaryKey).sort();
    expect(tagged).toEqual([
      'can_coaching_admin',
      'can_driver_pay_admin',
      'can_manage_applications',
      'can_manage_billing',
      'can_manage_driver_docs',
      'can_service_tasks',
    ]);
  });
});

describe('the nesting law', () => {

  it('cross-feature rows never nest and are exactly the config family', () => {
    const caps = tickable.filter((r) => r.kind === 'cross_feature');
    expect(caps.map((c) => primaryKey(c)).sort()).toEqual(
      ['can_manage_config_all', 'can_manage_config_role']);
    for (const c of caps) expect(c.parentKey, c.label).toBeUndefined();
  });

  it('an explicit parentKey may only target an EARLIER sub-feature', () => {
    // Depth-2 exists so a SUB-FEATURE can own its action/component rows.
    // Features use plain indentation; components may not own anything
    // (needing children is the graduation signal).  "Earlier" mirrors
    // toBlocks' incremental lookup: a forward reference would silently
    // render as an extra top-level row, so it fails HERE instead.
    for (const g of PERM_GROUPS) {
      const seen = new Map<string, Row>();
      for (const r of g.flags as Row[]) {
        if ('header' in r && r.header) continue;
        if (r.parentKey) {
          const parent = seen.get(r.parentKey);
          expect(parent, `${r.label}: parentKey ${r.parentKey} must be declared earlier in ${g.title}`).toBeTruthy();
          expect(parent!.kind, `${r.label} nests under ${parent!.label}`)
            .toBe('subfeature');
          expect(['action', 'component'], r.label).toContain(r.kind);
        }
        seen.set(primaryKey(r), r);
      }
    }
  });
});

describe('family order', () => {
  it('within every group, indented children follow the fixed order', () => {
    // Walk each group's flags: a run of indented rows after a parent must
    // have non-decreasing ORDER ranks (Manage before sub-features before
    // components; capabilities last).
    for (const g of PERM_GROUPS) {
      let run: Row[] = [];
      const check = () => {
        const ranks = run.map((r) => ORDER[r.kind!]);
        expect([...ranks].sort((a, b) => a - b),
          `${g.title}: ${run.map((r) => r.label).join(' → ')}`).toEqual(ranks);
        run = [];
      };
      for (const r of g.flags as Row[]) {
        if (r.indented) run.push(r);
        else if (run.length) check();
      }
      if (run.length) check();
    }
  });

  it('bare-verb "Manage" labels only ever appear indented under a parent', () => {
    for (const r of tickable.filter((x) => x.label === 'Manage')) {
      expect(r.indented || r.parentKey, `${primaryKey(r)}`).toBeTruthy();
      expect(r.kind).toBe('action');
    }
  });
});
