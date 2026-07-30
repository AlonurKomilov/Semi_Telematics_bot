/**
 * The verb-grid derivation — the "One role" lens's shape.
 *
 * Rows = feature families (sub-features and named actions nested
 * inside), columns = what a role can DO: View · Manage · Config.
 * DERIVED from permRows' typed tree — never a second hand-written
 * list, so the matrix and the role lens can't drift apart
 * (verbGrid.test.ts pins completeness: every tickable row appears
 * exactly once here).
 *
 * Honesty rules encoded:
 *  - a single write-level flag renders as ONE merged View+Manage cell
 *    ("one flag") — never a fake split;
 *  - Config cells appear only on features that HAVE config, and they
 *    ride the two family flags (docs/architecture/config.md) — the
 *    cell edits the capability row, visibly shared.
 */
import {
  DRIVER_RECORDS, DRIVER_TRUCK, GROUP_BLOCKS, isHeader, isScoped,
} from './permRows';
import type { Block, PermFlag, ScopedFlag, SimpleFlag } from './permRows';

export type TickRow = ScopedFlag | SimpleFlag;
export type ChildVerb = 'view' | 'manage' | 'merged';

export interface VerbChild { row: TickRow; verb: ChildVerb }
export interface VerbFamily {
  parent: TickRow;
  /** true = the parent's single flag is write-level → merged cell. */
  merged: boolean;
  /** The bare "Manage" action child, promoted into the parent's row. */
  manage?: TickRow;
  children: VerbChild[];
  /** The capability row this feature's config rides, when it has one. */
  configVia?: 'can_manage_config_all' | 'can_manage_config_role';
  configNote?: string;
}
export interface VerbBand { band: string; families: VerbFamily[] }

// Feature → which config-family flag tunes it (grows as features gain
// config; docs/architecture/config.md is the SSOT of members).
const CONFIG_VIA: Record<string, ['can_manage_config_all' | 'can_manage_config_role', string]> = {
  can_scorecard_all: ['can_manage_config_all', 'rules + pillar caps'],
  can_kpi: ['can_manage_config_all', 'grade thresholds'],
};

const rowKey = (r: TickRow): string => (isScoped(r) ? r.allKey : (r as SimpleFlag).key);

// One write-level flag = View and Manage are the same tick.  writeLevel
// marks the noun-labeled ones; governance/manage-named keys match by
// name; can_invite is a do-verb component of Settings.
const isMerged = (r: TickRow): boolean =>
  !isScoped(r) && (
    r.writeLevel === true ||
    /^can_(manage_|invite$)/.test((r as SimpleFlag).key) ||
    /_admin$/.test((r as SimpleFlag).key)
  );

const childVerb = (r: TickRow): ChildVerb => {
  if (r.kind === 'action') return 'manage';
  if (r.kind === 'subfeature') return 'view';
  return isMerged(r) ? 'merged' : 'view';   // components
};

function familyFrom(block: Block): VerbFamily {
  const parent = block.parent as TickRow;
  const fam: VerbFamily = { parent, merged: isMerged(parent), children: [] };
  const via = CONFIG_VIA[rowKey(parent)];
  if (via) { fam.configVia = via[0]; fam.configNote = via[1]; }
  for (const c of block.children) {
    const row = c.parent as TickRow;
    // The bare "Manage" child IS the parent's Manage cell; everything
    // else (named actions, sub-features, components) stays a child row.
    if (row.kind === 'action' && row.label === 'Manage' && !fam.manage) {
      fam.manage = row;
    } else {
      fam.children.push({ row, verb: childVerb(row) });
    }
    // Depth-2 (a sub-feature's own children) flattens under the family.
    for (const cc of c.children) {
      fam.children.push({ row: cc.parent as TickRow, verb: childVerb(cc.parent as TickRow) });
    }
  }
  return fam;
}

export interface VerbGrid { bands: VerbBand[]; capabilities: TickRow[] }

export function buildVerbGrid(): VerbGrid {
  const bands: VerbBand[] = [];
  const capabilities: TickRow[] = [];
  for (const g of GROUP_BLOCKS) {
    const families: VerbFamily[] = [];
    for (const block of g.blocks) {
      if (isHeader(block.parent)) {
        // A header's children (Settings / Costs components) are each
        // their own single-row family under the same band.
        for (const child of block.children) {
          const row = child.parent as TickRow;
          if (row.kind === 'capability') { capabilities.push(row); continue; }
          families.push(familyFrom(child));
        }
        continue;
      }
      const row = block.parent as TickRow;
      if (row.kind === 'capability') { capabilities.push(row); continue; }
      families.push(familyFrom(block));
    }
    if (families.length) bands.push({ band: g.title, families });
  }
  return { bands, capabilities };
}

/** Every tickable row the grid places — the completeness test's input. */
export function placedRows(grid: VerbGrid): PermFlag[] {
  const out: PermFlag[] = [...grid.capabilities];
  for (const b of grid.bands) for (const f of b.families) {
    out.push(f.parent);
    if (f.manage) out.push(f.manage);
    for (const c of f.children) out.push(c.row);
  }
  return out;
}


// ── The Driver, as a role the lens can open ────────────────────────
//
// A driver IS a role, so the One-role lens gives it a tab like any
// other.  What it can't do is share the staff row model: a driver's
// grants are always own-truck scoped, they never manage anything, and
// five of their flags (own documents, paystubs, coaching, loads, risk
// summary) deliberately have NO staff-matrix row at all.  So the tab
// renders these two bands instead of the verb families, and the matrix
// lens keeps its separate panel (a Driver *column* there is still
// nonsense).  Same storage key, same toggle, same save pipeline.
export const DRIVER_KEY = 'driver';

export interface DriverBand { title: string; note: string; rows: TickRow[] }

export function driverBands(): DriverBand[] {
  return [
    {
      title: 'Own truck',
      note: 'always their assigned truck only — never account-wide',
      rows: DRIVER_TRUCK as TickRow[],
    },
    {
      title: 'Own records',
      note: 'their own documents and history, nobody else\u2019s',
      rows: DRIVER_RECORDS as TickRow[],
    },
  ];
}
