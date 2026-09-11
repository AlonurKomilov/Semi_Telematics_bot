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
 *    ride the two family flags (capabilities/config/docs/ARCHITECTURE.md) — the
 *    cell edits the cross-feature row, visibly shared.
 */
import {
  DRIVER_RECORDS, DRIVER_SERVICES, DRIVER_TRUCK, GROUP_BLOCKS, isHeader, isScoped,
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
  /** The cross-feature rows this feature's config rides, if any.  A LIST,
   *  because a feature may ride BOTH scopes: Inventory's catalogue of what
   *  a vehicle owes is account-wide (data meaning), while which categories
   *  a role goes red about is that role's own (attention).  Showing one of
   *  the two would make the matrix misreport who can change what. */
  configVia?: ConfigFlag[];
  configNote?: Partial<Record<ConfigFlag, string>>;
}
export interface VerbBand { band: string; families: VerbFamily[] }

// Feature → which config-family flag tunes it (grows as features gain
// config; capabilities/config/docs/ARCHITECTURE.md is the SSOT of members).
// Every entry here is a feature whose account_settings rows are owned by
// the config family rather than by the feature's own Manage — which, per
// capabilities/settings_registry.py, is now ALL of them.  A feature
// appears the moment it has an account_settings key; the four below are
// the four that do.  Storage and Integrations were absent while their
// keys were owned by can_manage_storage / can_manage_integrations, so
// the matrix showed "–" in the Config column for settings that plainly
// existed — the owner could not see what granting Config actually moved.
type ConfigFlag = 'can_manage_config_all' | 'can_manage_config_role';

const CONFIG_VIA: Record<string, [ConfigFlag, string][]> = {
  // Vehicle source policy — field precedence + auto-pilot.  On the
  // VEHICLES row because that is where its gear lives now; it sat on
  // Integrations while the panel did, and a tick must always point at
  // the surface the grant actually opens.
  can_view_vehicles: [['can_manage_config_all', 'source precedence + auto-pilot']],
  // Alerts is a service row now; its Group delivery (forum topics,
  // per-type AI) is account_settings behind the config family.
  can_view_alerts: [['can_manage_config_all', 'group delivery — topics + per-type AI']],
  can_view_scorecards: [['can_manage_config_all', 'rules + pillar caps']],
  can_view_kpi: [['can_manage_config_all', 'grade thresholds']],
  can_manage_storage: [['can_manage_config_all', 'backend + disk quota']],
  can_manage_applications: [['can_manage_config_all', 'DQF export passphrase']],
  can_manage_account: [['can_manage_config_all', 'account-wide values']],
  // The first feature to ride BOTH scopes, and the reason configVia is a
  // list.  The catalogue is one truth for the account — whether a truck
  // is short its ELD is a fact about the truck.  The focus is each role's
  // own, because something going red for one role pulls another role's
  // attention onto what is not theirs.
  can_view_inventory: [
    ['can_manage_config_all', 'what a vehicle is expected to carry'],
    ['can_manage_config_role', 'which of it my role goes red about'],
  ],
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
  if (via) {
    fam.configVia = via.map(([flag]) => flag);
    fam.configNote = Object.fromEntries(via) as Partial<Record<ConfigFlag, string>>;
  }
  for (const c of block.children) {
    const row = c.parent as TickRow;
    // The bare "Manage" child IS the parent's Manage cell; everything
    // else (named actions, sub-features, components) stays a child row.
    // A MERGED parent's Manage column is the tie to its own tick, so a
    // promoted child there would never render — keep it a child row.
    if (!fam.merged && row.kind === 'action' && row.label === 'Manage' && !fam.manage) {
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

export interface VerbGrid { bands: VerbBand[]; crossFeature: TickRow[] }

export function buildVerbGrid(): VerbGrid {
  const bands: VerbBand[] = [];
  const crossFeature: TickRow[] = [];
  for (const g of GROUP_BLOCKS) {
    const families: VerbFamily[] = [];
    for (const block of g.blocks) {
      if (isHeader(block.parent)) {
        // A header's children (Settings / Costs components) are each
        // their own single-row family under the same band.
        for (const child of block.children) {
          const row = child.parent as TickRow;
          if (row.kind === 'cross_feature') { crossFeature.push(row); continue; }
          families.push(familyFrom(child));
        }
        continue;
      }
      const row = block.parent as TickRow;
      if (row.kind === 'cross_feature') { crossFeature.push(row); continue; }
      families.push(familyFrom(block));
    }
    if (families.length) bands.push({ band: g.title, families });
  }
  return { bands, crossFeature };
}

/** Every tickable row the grid places — the completeness test's input. */
export function placedRows(grid: VerbGrid): PermFlag[] {
  const out: PermFlag[] = [...grid.crossFeature];
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
      note: 'the PERSONAL tier — their own documents and history, nobody else\u2019s',
      rows: DRIVER_RECORDS as TickRow[],
    },
    {
      title: 'Services',
      note: 'The channels — the same rows the staff matrix carries, at the driver’s width.',
      rows: DRIVER_SERVICES as TickRow[],
    },
  ];
}


