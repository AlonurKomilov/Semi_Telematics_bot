/**
 * The "One role" lens — features down, verbs across (View · Manage ·
 * Config), roles as tabs, tier as a small switch.  Renders the SAME
 * rows and edits through the SAME toggle/diff/save pipeline as the
 * matrix lens: both are views over one pending-edits state, so a tick
 * here appears in the matrix, the sticky save bar and the confirm
 * dialog exactly like a matrix tick.
 */
import { useState } from 'react';
import { Eye, Lock } from 'lucide-react';
import { toneClasses } from '../../lib/status';
import { InfoTip, Tip } from '../../components/tooltip';
import { useRoleView } from '../../context/RoleViewContext';
import { buildVerbGrid } from './verbGrid';
import type { TickRow, VerbFamily } from './verbGrid';
import { isScoped } from './permRows';
import type { PermFlag } from './permRows';

const GRID = buildVerbGrid();

export interface RoleLensApi {
  roles: readonly string[];
  roleLabel: (role: string) => string;
  /** The role's two storage columns: [base, senior] keys + labels. */
  tierCols: (role: string) => { key: string; label: string }[];
  granted: (colKey: string, f: PermFlag) => boolean;
  changed: (colKey: string, f: PermFlag) => boolean;
  locked: (colKey: string, f: PermFlag) => boolean;
  onToggle: (colKey: string, f: PermFlag) => void;
}

export function RoleLens({ api }: { api: RoleLensApi }) {
  const { setRoleView, canSwitchView } = useSafeRoleSwitch();
  const [role, setRole] = useState<string>(api.roles[2] ?? api.roles[0]);
  const [tier, setTier] = useState(1);   // open on the senior tier: the delta is the point
  const cols = api.tierCols(role);
  const col = cols[Math.min(tier, cols.length - 1)];
  const seniorView = cols.length > 1 && col.key === cols[1].key;

  // Rows where the two tiers differ — the delta bar + row highlight.
  const rowDelta = (f: PermFlag): boolean =>
    cols.length > 1 && api.granted(cols[0].key, f) !== api.granted(cols[1].key, f);
  const famDelta = (fam: VerbFamily): boolean =>
    rowDelta(fam.parent) || (fam.manage ? rowDelta(fam.manage) : false) ||
    fam.children.some((c) => rowDelta(c.row));
  const deltaNames: string[] = [];
  for (const b of GRID.bands) for (const fam of b.families) {
    if (rowDelta(fam.parent)) deltaNames.push(fam.parent.label);
    if (fam.manage && rowDelta(fam.manage)) deltaNames.push(`Manage (${fam.parent.label})`);
    for (const c of fam.children) if (rowDelta(c.row)) deltaNames.push(`${c.row.label} (${fam.parent.label})`);
  }
  for (const cap of GRID.capabilities) if (rowDelta(cap)) deltaNames.push(cap.label);

  const chk = (f: TickRow, ariaSuffix: string) => {
    const on = api.granted(col.key, f);
    const lock = api.locked(col.key, f);
    const changed = api.changed(col.key, f);
    return (
      <button
        type="button"
        onClick={() => api.onToggle(col.key, f)}
        disabled={lock}
        aria-pressed={on}
        aria-label={`${f.label} — ${ariaSuffix}: ${on ? 'granted' : 'no access'}`}
        className={`inline-flex items-center justify-center w-5 h-5 rounded border transition ${
          changed ? 'ring-2 ring-primary/30 ' : ''
        }${lock
          ? 'bg-primary/40 border-primary/40 text-primary-foreground cursor-not-allowed'
          : on
            ? 'bg-primary border-primary text-primary-foreground'
            : 'bg-transparent border-border text-transparent hover:border-muted-foreground'
        }`}
      >
        {lock ? <Lock size={12} strokeWidth={2.5} /> : <CheckMark />}
      </button>
    );
  };
  const noflag = (
    <span className="inline-flex w-5 h-5 rounded border border-dashed border-border opacity-40" aria-hidden />
  );

  const famRow = (fam: VerbFamily) => {
    const delta = seniorView && famDelta(fam);
    return (
      <div key={rowId(fam.parent)}>
        <div className={rowCls(delta)}>
          <div className="min-w-0">
            <span className="text-sm font-medium">
              {fam.parent.label}
              {isScoped(fam.parent) && <span className="text-2xs text-muted-foreground ml-1">*</span>}
              {delta && <DeltaChip />}
            </span>
            {fam.parent.description && (
              <div className="text-2xs text-muted-foreground/70">{fam.parent.description}</div>
            )}
          </div>
          {fam.merged ? (
            <div className="col-span-2 text-center">
              {chk(fam.parent, 'view + manage')}
              <div className="text-3xs text-muted-foreground/70 mt-0.5">view + manage · one flag</div>
            </div>
          ) : (
            <>
              <div className="text-center">{chk(fam.parent, 'view')}</div>
              <div className="text-center">{fam.manage ? chk(fam.manage, 'manage') : noflag}</div>
            </>
          )}
          <div className="text-center">
            {fam.configVia ? (
              <span className="inline-flex items-center gap-1">
                {chk(capRow(fam.configVia), 'config')}
                <InfoTip size={12} label={`Rides ${capRow(fam.configVia).label} — one flag for every feature it covers${fam.configNote ? ` (here: ${fam.configNote})` : ''}.`} />
              </span>
            ) : noflag}
          </div>
        </div>
        {fam.children.map((c) => {
          const cDelta = seniorView && rowDelta(c.row);
          return (
            <div key={rowId(c.row)} className={rowCls(cDelta)}>
              <div className="min-w-0 pl-4 border-l-2 border-border ml-0.5">
                <span className="text-sm text-muted-foreground">{c.row.label}{cDelta && <DeltaChip />}</span>
                {c.row.description && (
                  <div className="text-2xs text-muted-foreground/60">{c.row.description}</div>
                )}
              </div>
              {c.verb === 'merged' ? (
                <div className="col-span-2 text-center">
                  {chk(c.row, 'view + manage')}
                  <div className="text-3xs text-muted-foreground/70 mt-0.5">one flag</div>
                </div>
              ) : (
                <>
                  <div className="text-center">{c.verb === 'view' ? chk(c.row, 'view') : noflag}</div>
                  <div className="text-center">{c.verb === 'manage' ? chk(c.row, 'manage') : noflag}</div>
                </>
              )}
              <div className="text-center">{noflag}</div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div>
      {/* Role tabs + preview */}
      <div className="flex items-center gap-1.5 flex-wrap px-4 pt-3">
        {api.roles.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setRole(r)}
            className={`text-xs px-3 py-1 rounded-full border transition ${
              r === role
                ? 'bg-primary text-primary-foreground border-primary font-medium'
                : 'border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            {api.roleLabel(r)}
          </button>
        ))}
        <span className="flex-1" />
        {canSwitchView && (
          <button
            type="button"
            onClick={() => setRoleView(role)}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1 rounded-md border border-border text-foreground hover:bg-muted"
          >
            <Eye size={14} aria-hidden /> Preview dashboard as {api.roleLabel(role)}
          </button>
        )}
      </div>

      {/* Tier switch */}
      {cols.length > 1 && (
        <div className="flex items-center gap-2 px-4 pt-2.5">
          <span className="text-xs text-muted-foreground">Tier</span>
          <div className="inline-flex bg-muted border border-border rounded-md p-0.5">
            {cols.map((c, i) => (
              <button
                key={c.key}
                type="button"
                onClick={() => setTier(i)}
                className={`text-xs px-2.5 py-1 rounded transition ${
                  i === Math.min(tier, cols.length - 1)
                    ? 'bg-card text-foreground font-medium shadow-sm'
                    : 'text-muted-foreground'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          {seniorView && (
            <span className="text-2xs text-muted-foreground">
              rows the {cols[0].label} tier lacks are highlighted
            </span>
          )}
        </div>
      )}

      {/* The tier delta, as a sentence */}
      {cols.length > 1 && (
        <p className={`mx-4 mt-2.5 px-3 py-1.5 rounded-md text-xs flex items-baseline gap-1 min-w-0 ${toneClasses(deltaNames.length ? 'ok' : 'neutral')}`}>
          {/* One line, always — tab clicks must not move the grid below
              (the full list rides the tooltip). */}
          {deltaNames.length ? (
            <>
              <span className="font-semibold shrink-0">{cols[1].label} adds {deltaNames.length} grant{deltaNames.length > 1 ? 's' : ''}:</span>
              <Tip label={deltaNames.join(' · ')}>
                <span className="truncate min-w-0">{deltaNames.join(' · ')}</span>
              </Tip>
            </>
          ) : (
            <span className="truncate min-w-0">{cols[1].label} currently adds nothing beyond {cols[0].label} for this role.</span>
          )}
          {role === 'owner' && (
            <Tip label="Primary also exclusively holds the Owner powers — Manage owners, Delete / restore account — which aren't flags and can never be granted to a co-owner (see the matrix lens's Owner powers rows).">
              <span className="shrink-0 text-2xs opacity-80 underline decoration-dotted cursor-help">+ Owner powers</span>
            </Tip>
          )}
        </p>
      )}

      {/* Verb grid */}
      <div className="px-4 pb-4">
        <div className="grid grid-cols-[1fr_84px_84px_96px] gap-x-2 pt-3 pb-1.5 border-b border-border sticky top-0 bg-card z-10">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Feature</span>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">View</span>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">Manage</span>
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">Config</span>
        </div>
        {GRID.bands.map((b) => (
          <div key={b.band}>
            <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              {b.band}
            </div>
            {b.families.map(famRow)}
          </div>
        ))}
        <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          Configuration (the family flags themselves)
        </div>
        {GRID.capabilities.map((cap) => {
          const delta = seniorView && rowDelta(cap);
          return (
            <div key={rowId(cap)} className={rowCls(delta)}>
              <div className="min-w-0">
                <span className="text-sm font-medium">{cap.label}{delta && <DeltaChip />}</span>
                {cap.description && <div className="text-2xs text-muted-foreground/70">{cap.description}</div>}
              </div>
              <div className="text-center">{noflag}</div>
              <div className="text-center">{noflag}</div>
              <div className="text-center">{chk(cap, 'config')}</div>
            </div>
          );
        })}
        <p className="text-2xs text-muted-foreground mt-3">
          A dashed square means this feature has no flag of that verb — nothing to grant, not a denial.
          * scoped feature — whose data is set per-user in Team Management.
        </p>
      </div>
    </div>
  );
}

// ── helpers ────────────────────────────────────────────────────────

const rowId = (r: TickRow): string => (isScoped(r) ? r.allKey : (r as { key: string }).key);
const capRow = (key: 'can_manage_config_all' | 'can_manage_config_role'): TickRow =>
  GRID.capabilities.find((c) => rowId(c) === key)!;
const rowCls = (delta: boolean): string =>
  `grid grid-cols-[1fr_84px_84px_96px] gap-x-2 items-center py-1.5 border-t border-border ${
    delta ? 'bg-ok/10 rounded' : ''
  }`;

function DeltaChip() {
  return (
    <span className="ml-2 text-3xs font-semibold uppercase tracking-wide text-ok">
      manager adds
    </span>
  );
}
function CheckMark() {
  return <svg viewBox="0 0 12 12" className="w-3 h-3" aria-hidden><path d="M2 6.5 4.8 9 10 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

// The view switcher's REAL contract (the one PersonaSelector uses):
// canSwitch gates who may preview; switchView(role) does it.
function useSafeRoleSwitch(): { canSwitchView: boolean; setRoleView: (r: string) => void } {
  const { canSwitch, switchView } = useRoleView();
  return { canSwitchView: canSwitch, setRoleView: switchView };
}
