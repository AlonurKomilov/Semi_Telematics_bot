/**
 * The "One role" lens — features down, verbs across (View · Manage ·
 * Config), roles as tabs, tier as a small switch.  Renders the SAME
 * rows and edits through the SAME toggle/diff/save pipeline as the
 * matrix lens: both are views over one pending-edits state, so a tick
 * here appears in the matrix, the sticky save bar and the confirm
 * dialog exactly like a matrix tick.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Eye, Link2, Lock } from 'lucide-react';
import { toneClasses } from '../../lib/status';
import { Tip } from '../../components/tooltip';
import { usePreference } from '../../preferences';
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
  // Last role opened, per device — an owner returning to the page almost
  // always continues on the role they were editing.
  const { value: lastRole, setValue: setLastRole } = usePreference('permissions.role');
  const [role, setRoleState] = useState<string>(() =>
    (lastRole && api.roles.includes(lastRole) ? lastRole : (api.roles[2] ?? api.roles[0])));
  const setRole = (r: string) => { setRoleState(r); setLastRole(r); };
  // Open on the BASE tier: that's where most people on the role sit, so
  // the first tick lands on the row the owner meant.  The delta sentence
  // below reports the senior tier either way — no information is lost by
  // not starting there.
  const [tier, setTier] = useState(0);
  const cols = api.tierCols(role);
  const col = cols[Math.min(tier, cols.length - 1)];
  const seniorView = cols.length > 1 && col.key === cols[1].key;

  // Flags whose value differs between the tiers — the delta sentence
  // and the per-cell highlight.
  const rowDelta = (f: PermFlag): boolean =>
    cols.length > 1 && api.granted(cols[0].key, f) !== api.granted(cols[1].key, f);
  const deltaNames: string[] = [];
  for (const b of GRID.bands) for (const fam of b.families) {
    if (rowDelta(fam.parent)) deltaNames.push(fam.parent.label);
    if (fam.manage && rowDelta(fam.manage)) deltaNames.push(`Manage (${fam.parent.label})`);
    for (const c of fam.children) if (rowDelta(c.row)) deltaNames.push(`${c.row.label} (${fam.parent.label})`);
  }
  for (const cap of GRID.capabilities) if (rowDelta(cap)) deltaNames.push(cap.label);

  const chk = (f: TickRow, ariaSuffix: string, soft = false) => {
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
            ? soft
              // Derived tick: a primary-coloured check on a light primary
              // wash reads on BOTH themes (primary-foreground would go
              // invisible against a 20% wash on the light theme).
              ? 'bg-primary/20 border-primary/40 text-primary'
              : 'bg-primary border-primary text-primary-foreground'
            : `bg-transparent text-transparent hover:border-muted-foreground ${soft ? 'border-border/60' : 'border-border'}`
        }`}
      >
        {lock ? <Lock size={12} strokeWidth={2.5} /> : <CheckMark />}
      </button>
    );
  };
  const noflag = (
    <span className="inline-flex w-5 h-5 rounded border border-dashed border-border opacity-40" aria-hidden />
  );
  const emptyCell = <div className="text-center">{noflag}</div>;

  // One verb cell.  The tint marks THIS cell as what the senior tier adds —
  // never the whole row: a row whose View is identical in both tiers must
  // not claim the tier "adds" it just because its Manage differs.
  const verbCell = (
    f: TickRow | null, ariaSuffix: string, extra?: ReactNode, soft = false,
  ): ReactNode => {
    if (!f) return emptyCell;
    const delta = seniorView && rowDelta(f);
    return (
      <div className={`text-center py-0.5 ${delta ? 'bg-ok/10 rounded' : ''}`}>
        <span className="inline-flex items-center gap-1">{chk(f, ariaSuffix, soft)}{extra}</span>
      </div>
    );
  };

  // A single write-level flag covers View AND Manage, so BOTH columns show
  // the real state — a column a reader can't trust is worse than a little
  // repetition, and the first draft's tie line read as "—", i.e. "nothing
  // here", the exact opposite of what it meant.  The Manage side is drawn
  // softer and carries the link glyph — the same "this control isn't
  // local" mark the shared config cells use — because either tick toggles
  // the one flag.
  const linkedCell = (f: TickRow): ReactNode => verbCell(
    f, 'manage — the same flag as view',
    (
      <Tip label="One flag covers View and Manage for this feature — toggling either changes both.">
        <span className="inline-flex text-muted-foreground"><Link2 size={12} aria-hidden /></span>
      </Tip>
    ),
    true,
  );

  // The config cell edits a flag SHARED with other features — a link
  // glyph says so before the click (an ⓘ would only promise an
  // explanation; the shape has to say "not local").
  const configCell = (fam: VerbFamily): ReactNode => {
    if (!fam.configVia) return emptyCell;
    const cap = capRow(fam.configVia);
    return verbCell(cap, 'config', (
      <Tip label={`Shared control — the same flag as “${cap.label}”${fam.configNote ? ` (here: ${fam.configNote})` : ''}. Changing it here changes it everywhere that flag appears.`}>
        <span className="inline-flex text-muted-foreground"><Link2 size={12} aria-hidden /></span>
      </Tip>
    ));
  };

  const famRow = (fam: VerbFamily) => {
    // The chip names what the tier adds, so it belongs to the row whose
    // OWN flag differs — never to a parent whose child's flag differs.
    const ownDelta = seniorView && rowDelta(fam.parent);
    return (
      <div key={rowId(fam.parent)}>
        <div className={rowCls()}>
          <div className="min-w-0">
            <span className="text-sm font-medium">
              {fam.parent.label}
              {isScoped(fam.parent) && <span className="text-2xs text-muted-foreground ml-1">*</span>}
              {ownDelta && <DeltaChip />}
            </span>
            {fam.parent.description && (
              <div className="text-2xs text-muted-foreground/70">{fam.parent.description}</div>
            )}
          </div>
          {fam.merged ? (
            <>{verbCell(fam.parent, 'view')}{linkedCell(fam.parent)}</>
          ) : (
            <>{verbCell(fam.parent, 'view')}{verbCell(fam.manage ?? null, 'manage')}</>
          )}
          {configCell(fam)}
        </div>
        {fam.children.map((c) => {
          const cDelta = seniorView && rowDelta(c.row);
          return (
            <div key={rowId(c.row)} className={rowCls()}>
              <div className="min-w-0 pl-4 border-l-2 border-border ml-0.5">
                <span className="text-sm text-muted-foreground">{c.row.label}{cDelta && <DeltaChip />}</span>
                {c.row.description && (
                  <div className="text-2xs text-muted-foreground/60">{c.row.description}</div>
                )}
              </div>
              {c.verb === 'merged' ? (
                <>{verbCell(c.row, 'view')}{linkedCell(c.row)}</>
              ) : (
                <>
                  {verbCell(c.verb === 'view' ? c.row : null, 'view')}
                  {verbCell(c.verb === 'manage' ? c.row : null, 'manage')}
                </>
              )}
              {emptyCell}
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
              cells the {cols[0].label} tier lacks are highlighted
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
          Configuration
        </div>
        {GRID.capabilities.map((cap) => {
          const delta = seniorView && rowDelta(cap);
          return (
            <div key={rowId(cap)} className={rowCls()}>
              <div className="min-w-0">
                <span className="text-sm font-medium">{cap.label}{delta && <DeltaChip />}</span>
                {cap.description && <div className="text-2xs text-muted-foreground/70">{cap.description}</div>}
              </div>
              {emptyCell}
              {emptyCell}
              {verbCell(cap, 'config')}
            </div>
          );
        })}
        <p className="text-2xs text-muted-foreground mt-3">
          A dashed square means this feature has no flag of that verb — nothing to grant, not a denial.
          Where one flag covers both verbs, the Manage tick is drawn softer with a link
          mark (<Link2 size={12} className="inline align-[-2px]" aria-hidden />) — either tick toggles both.
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
const rowCls = (): string =>
  'grid grid-cols-[1fr_84px_84px_96px] gap-x-2 items-center py-1.5 border-t border-border';

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
