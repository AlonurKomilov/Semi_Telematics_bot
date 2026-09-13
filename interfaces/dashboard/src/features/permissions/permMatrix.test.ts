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
import { PERM_GROUPS, PARENT_KEY, PARENT_LABEL, contextLabel } from './permRows';
import lensSrc from './RoleLens.tsx?raw';

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
    // Coaching, Driver Pay and Drivers left this set with the person fold:
    // each is a View row with a Manage action now.  Dispatcher incentive
    // runs & payouts are compensation — one write-level flag beside
    // driver_pay's, deliberately not can_view_kpi.
    expect(tagged).toEqual([
      'can_manage_applications',
      'can_manage_billing',
      'can_manage_service_tasks',
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

describe('PARENT_KEY — what a revoked child leaves behind', () => {
  it('every child row hangs under its parent by KEY, not only by label', () => {
    // The confirm dialog reads this to say "Manage off — the read stays".
    // Without the key it could only print a label and never ask whether
    // the parent is still granted.
    for (const [child, label] of Object.entries(PARENT_LABEL)) {
      expect(PARENT_KEY[child], `${child} has a parent label but no parent key`).toBeTruthy();
      const parentRow = tickable.find((r) => primaryKey(r) === PARENT_KEY[child]);
      expect(parentRow, `${child} → ${PARENT_KEY[child]} is not a row`).toBeTruthy();
      expect(parentRow!.label).toBe(label);
    }
  });

  it('the Manage actions of the folded families hang under their view verb', () => {
    // The person fold turned three write-level feature rows into
    // View + Manage; the dialog's retained-read line depends on the tie.
    expect(PARENT_KEY['can_manage_coaching']).toBe('can_view_coaching');
    expect(PARENT_KEY['can_manage_driver_pay']).toBe('can_view_driver_pay');
    expect(PARENT_KEY['can_manage_driver_docs']).toBe('can_view_driver_docs');
    expect(PARENT_KEY['can_manage_loads']).toBe('can_view_loads');
  });

  it('a top-level feature row has no parent', () => {
    expect(PARENT_KEY['can_view_coaching']).toBeUndefined();
    expect(PARENT_KEY['can_view_loads']).toBeUndefined();
  });
});

/**
 * What a bare verb is CALLED where the row cannot be seen.
 *
 * Thirteen rows in this matrix are labelled just "Manage", and two of
 * them sit in one family — the live map's own, and the POI layers'.  On
 * screen the row they hang under tells them apart.  In an aria-label
 * there is no row: both announced "Manage — manage: granted", and a
 * screen-reader user had no way to know which tick they were on.
 *
 * permRows wrote `contextLabel` for exactly this, in exactly these
 * words: "so a bare-verb child is never ambiguous where rows are listed
 * OUT of tree context".  An aria-label is out of tree context.
 */
describe('a bare verb is never announced ambiguously', () => {
  const byKey = (k: string) => {
    const row = tickable.find((r) => primaryKey(r) === k);
    expect(row, k).toBeTruthy();
    return row!;
  };

  it('tells the two Manages of the Live Map family apart', () => {
    expect(contextLabel(byKey('can_manage_live_map') as never)).toBe('Live Map · Manage');
    expect(contextLabel(byKey('can_manage_poi_layers') as never)).toBe('POI Layers · Manage');
  });

  it('gives EVERY bare "Manage" in the matrix a distinct name', () => {
    // Not just the pair that caught it: any two families whose Manage
    // rows collapsed to one name would have the same problem.
    const names = tickable
      .filter((r) => r.label === 'Manage')
      .map((r) => contextLabel(r as never));
    expect(names.length).toBeGreaterThan(1);
    expect(new Set(names).size, names.join(' / ')).toBe(names.length);
  });

  it('and the lens announces a tick by that name, not by the bare label', () => {
    expect(lensSrc).toContain('contextLabel(f)');
    expect(lensSrc).not.toMatch(/aria-label=\{`\$\{f\.label\} —/);
  });
});

/**
 * A "Manage" promoted into a COLUMN has no row, so the sentence saying
 * what it grants has nowhere to print.  All thirteen have one written,
 * and none of them was reachable — including the live map's "Reserved
 * for map-level settings — nothing uses it yet", which is the whole
 * reason that row is visible at all.  A tick promising a power it does
 * not carry, with the disclaimer unreadable, is worse than no row.
 */
describe('a promoted Manage can still be read', () => {
  it('every one of them has something to say', () => {
    const promoted = tickable.filter((r) => r.label === 'Manage') as { description?: string }[];
    for (const r of promoted) {
      expect(r.description, (r as unknown as Row).key).toBeTruthy();
    }
  });

  it('and the lens hands it to the reader on the cell', () => {
    expect(lensSrc).toContain('const manageCell');
    expect(lensSrc).toMatch(/f\.description \? <Tip label=\{f\.description\}>/);
    // Both promotion sites go through it — the family row's Manage and
    // a sub-feature's own.
    expect(lensSrc).toContain('manageCell(fam.manage ?? null, closed)');
    expect(lensSrc).toContain('manageCell(c.manage, closed, true)');
  });
});
