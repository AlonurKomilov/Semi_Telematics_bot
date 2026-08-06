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
 *    cell edits the cross-feature row, visibly shared.
 */
import { FEATURE_CATALOG } from '../../config/featureCatalog';
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
  /** The cross-feature row this feature's config rides, if any. */
  configVia?: 'can_manage_config_all' | 'can_manage_config_role';
  configNote?: string;
}
export interface VerbBand { band: string; families: VerbFamily[] }

// Feature → which config-family flag tunes it (grows as features gain
// config; docs/architecture/config.md is the SSOT of members).
// Every entry here is a feature whose account_settings rows are owned by
// the config family rather than by the feature's own Manage — which, per
// capabilities/settings_registry.py, is now ALL of them.  A feature
// appears the moment it has an account_settings key; the four below are
// the four that do.  Storage and Integrations were absent while their
// keys were owned by can_manage_storage / can_manage_integrations, so
// the matrix showed "–" in the Config column for settings that plainly
// existed — the owner could not see what granting Config actually moved.
const CONFIG_VIA: Record<string, ['can_manage_config_all' | 'can_manage_config_role', string]> = {
  can_scorecard_all: ['can_manage_config_all', 'rules + pillar caps'],
  can_kpi: ['can_manage_config_all', 'grade thresholds'],
  can_manage_storage: ['can_manage_config_all', 'backend + disk quota'],
  can_manage_integrations: ['can_manage_config_all', 'provider precedence'],
  can_manage_applications: ['can_manage_config_all', 'DQF export passphrase'],
  can_manage_account: ['can_manage_config_all', 'account-wide values'],
};

// Services can have config too, and the matrix could not say so.
//
// A service row renders "always on for every role, nothing to grant" and
// four dashes, which was true when a service was only an inbox. Alerts
// now has Group delivery — forum topics and per-type AI, written to
// account_settings behind can_manage_config_all — so "nothing to grant"
// was FALSE for the one column that mattered. An owner reading the matrix
// could not discover that granting Config · account-wide changes how
// alerts reach a Telegram group.
//
// Access to the service stays derived; only its CONFIG is grantable.
const SERVICE_CONFIG_VIA: Record<string, ['can_manage_config_all' | 'can_manage_config_role', string]> = {
  alerts: ['can_manage_config_all', 'group delivery — topics + per-type AI'],
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
  ];
}


// ── Services — the band above everything grantable ─────────────────
//
// Alerts / AI / Reports are always-on and their access is DERIVED
// (capabilities/permissions/roles.derive_service_perms; the save endpoint
// strips those flags), so they have nothing to tick.  They lead the grid
// anyway: the page then reads top-down as the model — what every role
// always has, then what you grant, then what you configure.
//
// The membership comes from the CATALOG (entries whose kind is
// 'service'), never a second hand-written list; verbGrid.test.ts fails if
// a service ships without copy here.
const SERVICE_COPY: Record<string, string> = {
  alerts: 'The inbox every role has. It shows the alerts for whichever features the role can see — disable a feature and just its alerts drop out.',
  ai_assistant: 'Available to every role. Each tool answers only from data the role can already see, so a tool\u2019s access is simply its feature\u2019s access.',
  reports: 'The hub and its scheduled-report subscription are open to every role; which tabs appear follows the role\u2019s features. The report TYPES (Risk Summary, Cost Reports) stay grantable below.',
};

export interface ServiceRow {
  id: string;
  label: string;
  note: string;
  /** Set when the service has account/role config behind a family flag. */
  configVia?: 'can_manage_config_all' | 'can_manage_config_role';
  configNote?: string;
}

export function serviceRows(): ServiceRow[] {
  return FEATURE_CATALOG
    .filter((e) => e.kind === 'service')
    .map((e) => ({
      id: e.id,
      // nav.<id> is the i18n key; the plain label is its tail, which is
      // what this read-only band needs (no translation plumbing for a
      // row nobody can act on).
      label: e.id.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join(' '),
      note: SERVICE_COPY[e.id] ?? '',
      configVia: SERVICE_CONFIG_VIA[e.id]?.[0],
      configNote: SERVICE_CONFIG_VIA[e.id]?.[1],
    }));
}
