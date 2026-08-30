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
import { Check, Eye, Link2, Lock } from 'lucide-react';
import { InfoTip, Tip } from '../../components/tooltip';
import { usePreference } from '../../preferences';
import { useRoleView } from '../../context/RoleViewContext';
import { DRIVER_KEY, buildVerbGrid, driverBands, serviceRows } from './verbGrid';
import type { TickRow, VerbFamily } from './verbGrid';
import { isScoped } from './permRows';
import type { PermFlag } from './permRows';
import { Badge } from '@/components/ui/badge';

const GRID = buildVerbGrid();
const HEAD_COLS = 'grid grid-cols-[1fr_84px_84px_76px_84px]';
type ConfigScope = 'can_manage_config_role' | 'can_manage_config_all';
const DRIVER_BANDS = driverBands();
const SERVICES = serviceRows();

export interface RoleLensApi {
  roles: readonly string[];
  roleLabel: (role: string) => string;
  /** The role's two storage columns: [base, senior] keys + labels. */
  tierCols: (role: string) => { key: string; label: string }[];
  granted: (colKey: string, f: PermFlag) => boolean;
  changed: (colKey: string, f: PermFlag) => boolean;
  locked: (colKey: string, f: PermFlag) => boolean;
  onToggle: (colKey: string, f: PermFlag) => void;
  /** Primary-owner-only ACTIONS (is_primary_owner gates, not flags) —
   *  shown read-only on the Owner tab. */
  ownerPowers: { key: string; label: string; description: string }[];
  /** Every role label that holds this flag — the cross-role question the
   *  matrix used to answer by column-scanning. */
  heldBy: (f: PermFlag) => string[];
  /** How many ACTIVE people hold each role.  Undefined while the payload
   *  is still loading — a tab shows no number rather than a wrong 0. */
  people?: Record<string, number>;
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
  const isDriver = role === DRIVER_KEY;
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
  for (const cap of GRID.crossFeature) if (rowDelta(cap)) deltaNames.push(cap.label);

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
        // Hit box split from paint. A 24px TICK would widen every column
        // of the matrix, so the 20px box stays what is drawn and the
        // button around it carries the WCAG 2.5.8 target; -m-0.5 gives
        // the grid back the rhythm the paint had.
        className="inline-flex items-center justify-center min-h-tap min-w-tap -m-0.5 disabled:cursor-not-allowed"
      >
        <span
          aria-hidden
          className={`inline-flex items-center justify-center w-5 h-5 rounded border transition ${
            changed ? 'ring-2 ring-primary/30 ' : ''
          }${lock
            // `text-foreground`, not `text-primary-foreground`: the note
            // on the sibling branch below already explains why the label
            // token cannot sit on a wash, and this branch was doing it
            // anyway — 1.78:1 on the light theme. text-foreground reads
            // on both (10.68 light, 8.42 dark).
            ? 'bg-primary/40 border-primary/40 text-foreground'
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
          {lock ? <Lock className="size-3" strokeWidth={2.5} /> : <CheckMark />}
        </span>
      </button>
    );
  };
  // "Nothing to grant here" is an en dash — the SAME mark the matrix
  // lens uses on its owners-only rows.  A bespoke dashed square would be
  // a second vocabulary for one meaning on one page.
  const noflag = (
    <span className="inline-flex text-muted-foreground/40 text-xs leading-5" aria-hidden>–</span>
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
        <span className="inline-flex text-muted-foreground"><Link2 className="size-3" aria-hidden /></span>
      </Tip>
    ),
    true,
  );

  // The config cell edits a flag SHARED with other features — a link
  // glyph says so before the click (an ⓘ would only promise an
  // explanation; the shape has to say "not local").
  // Two cells: one per config scope, so the tick's column is the answer.
  const configCells = (fam: VerbFamily): ReactNode => (
    <>
      {configCell(fam, 'can_manage_config_role')}
      {configCell(fam, 'can_manage_config_all')}
    </>
  );
  const configCell = (fam: VerbFamily, scope: ConfigScope): ReactNode =>
    configCellFor(fam.configVia, fam.configNote, scope);

  // Shared by feature families and SERVICE rows: a service is always-on
  // but can still own config (Alerts → Group delivery), and its row used
  // to render four dashes that said otherwise.
  const configCellFor = (
    via: ConfigScope | undefined, note: string | undefined, scope: ConfigScope,
  ): ReactNode => {
    if (via !== scope) return emptyCell;
    const cap = capRow(via);
    return verbCell(cap, 'config', (
      <Tip label={`Shared control — the same flag as “${cap.label}”${note ? ` (here: ${note})` : ''}. Changing it here changes it everywhere that flag appears.`}>
        <span className="inline-flex text-muted-foreground"><Link2 className="size-3" aria-hidden /></span>
      </Tip>
    ));
  };

  // Which OTHER roles hold this grant.  Inline and tiny: the answer the
  // deleted matrix gave by eye-scanning a column, now in words — and it
  // must not add a line, or every row's rhythm changes (S2).
  const alsoChip = (f: TickRow): ReactNode => {
    const me = api.roleLabel(role);
    const others = api.heldBy(f).filter((r) => r !== me);
    if (!others.length) return null;
    return (
      <Tip label={`Also granted to: ${others.join(', ')}`}>
        <span className="ml-2 text-2xs text-muted-foreground/70 cursor-help">
          {others.length} other{others.length > 1 ? 's' : ''}
        </span>
      </Tip>
    );
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
              {alsoChip(fam.parent)}
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
          {configCells(fam)}
        </div>
        {fam.children.map((c) => {
          const cDelta = seniorView && rowDelta(c.row);
          return (
            <div key={rowId(c.row)} className={rowCls()}>
              <div className="min-w-0 pl-4 border-l-2 border-border ml-0.5">
                <span className="text-sm text-muted-foreground">
                  {c.row.label}{cDelta && <DeltaChip />}{alsoChip(c.row)}
                </span>
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
        {[...api.roles, DRIVER_KEY].map((r) => {
          // The blast radius of everything below: a toggle on a role
          // seven people hold is a different act from the same toggle on
          // a role nobody holds, and the two used to look identical.
          const count = api.people?.[r];
          const empty = count === 0;
          return (
          <button
            key={r}
            type="button"
            onClick={() => setRole(r)}
            // The digit is a headcount, not part of the role's name —
            // say so, or a screen reader reads "Fleet 7" as one label.
            aria-label={count === undefined
              ? api.roleLabel(r)
              : `${api.roleLabel(r)} — ${count} ${count === 1 ? 'person' : 'people'}`}
            className={`text-xs px-3 py-1 rounded-full border transition ${
              r === role
                ? 'bg-primary text-primary-foreground border-primary font-medium'
                : `border-border hover:text-foreground ${
                    // A role with nobody in it recedes rather than
                    // hiding — an owner still configures it for the
                    // people they are about to invite.
                    empty ? 'text-muted-foreground/60' : 'text-muted-foreground'}`
            } min-h-tap`}
          >
            {api.roleLabel(r)}
            {count !== undefined && (
              <span className={`ml-1.5 tabular-nums ${
                r === role ? 'opacity-80' : 'opacity-70'}`}>{count}</span>
            )}
          </button>
          );
        })}
        <span className="flex-1" />
        {canSwitchView && !isDriver && (
          <button
            type="button"
            onClick={() => setRoleView(role)}
            aria-label={`Preview the dashboard as ${api.roleLabel(role)}`}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1 rounded-md border border-border text-foreground hover:bg-muted min-h-tap"
          >
            <Eye className="size-3.5" aria-hidden /> Preview dashboard
          </button>
        )}
      </div>

      {/* Tier switch + the delta, on ONE line.  The delta belongs beside
          the control that changes tiers, not in a full-width banner above
          the grid — and the "highlighted cells" hint is gone: the pill
          already names what the senior tier adds, and those are the cells
          tinted in the same green.  Long lists truncate into the tooltip
          so this row can never wrap and shove the grid down. */}
      {cols.length > 1 && !isDriver && (
        <div className="flex items-center gap-2 px-4 pt-2.5 min-w-0">
          <span className="text-xs text-muted-foreground shrink-0">Tier</span>
          <div className="inline-flex bg-muted border border-border rounded-md p-0.5 shrink-0">
            {cols.map((c, i) => (
              <button
                key={c.key}
                type="button"
                onClick={() => setTier(i)}
                className={`text-xs px-2.5 py-1 rounded transition ${
                  i === Math.min(tier, cols.length - 1)
                    ? 'bg-card text-foreground font-medium shadow-sm'
                    : 'text-muted-foreground'
                } min-h-tap`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <Badge tone={deltaNames.length ? 'ok' : 'neutral'} className="items-baseline min-w-0">
            {deltaNames.length ? (
              <>
                <span className="font-semibold shrink-0">
                  {cols[1].label} adds {deltaNames.length}:
                </span>
                <Tip label={deltaNames.join(' · ')}>
                  <span className="truncate min-w-0">{deltaNames.join(' · ')}</span>
                </Tip>
              </>
            ) : (
              <span className="truncate min-w-0">
                {cols[1].label} adds nothing beyond {cols[0].label}
              </span>
            )}
            {role === 'owner' && (
              <Tip label="Primary also exclusively holds the Owner powers — Manage owners, Delete / restore account. They aren't flags and can never be granted to a co-owner; see the Owner powers band below.">
                <span className="shrink-0 underline decoration-dotted cursor-help">+ Owner powers</span>
              </Tip>
            )}
          </Badge>
        </div>
      )}

      {/* Verb grid */}
      <div className="px-4 pb-4">
        {/* Two-level header for CONFIG alone: its two columns ARE the two
            config flags, so a tick's COLUMN says which scope it is and
            features sharing a flag line up under it — position carries
            what the link mark used to carry by itself.  The scope
            descriptions live on these headers because the column is the
            flag. */}
        <div className="sticky top-0 bg-card z-10 border-b border-border pt-3 pb-1.5">
          <div className={`${HEAD_COLS} gap-x-2`}>
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Feature</span>
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">View</span>
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">Manage</span>
            <span className="col-span-2 text-xs font-medium uppercase tracking-wide text-muted-foreground text-center">Config</span>
          </div>
          <div className={`${HEAD_COLS} gap-x-2`}>
            <span /><span /><span />
            <span className="text-2xs text-muted-foreground/70 text-center inline-flex items-center justify-center gap-0.5">
              own role
              <InfoTip size={12} label="Team-default page layouts for the holder's OWN role — the page gear's “Team default” block. General-settings holders can set any role's." />
            </span>
            <span className="text-2xs text-muted-foreground/70 text-center inline-flex items-center justify-center gap-0.5">
              account-wide
              <InfoTip size={12} label="A feature's SHARED settings, one truth for everyone: scorecard rules + pillar caps, KPI grade thresholds, and every future feature setting." />
            </span>
          </div>
        </div>
        {isDriver && (
          <p className="text-2xs text-muted-foreground pt-2.5 inline-flex items-center gap-1">
            Mini app only — every grant is View, always their own truck.
            <InfoTip size={12} label="Drivers never manage anything and work in the Telegram mini app only. Changes reach every driver's app on next load. The Alerts inbox and the AI assistant come automatically with the Vehicle grant." />
          </p>
        )}
        {isDriver && DRIVER_BANDS.map((b) => (
          <div key={b.title}>
            <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              {b.title} <span className="normal-case tracking-normal text-muted-foreground/70">— {b.note}</span>
            </div>
            {b.rows.map((r) => (
              <div key={rowId(r)} className={rowCls()}>
                <div className="min-w-0">
                  <span className="text-sm font-medium">{r.label}</span>
                  {r.description && (
                    <div className="text-2xs text-muted-foreground/70">{r.description}</div>
                  )}
                </div>
                {verbCell(r, 'view')}
                {emptyCell}
                {emptyCell}
                {emptyCell}
              </div>
            ))}
          </div>
        ))}
        {!isDriver && (
          <>
            <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground inline-flex items-center gap-1 w-[calc(100%+2rem)]">
              Services
              <span className="normal-case tracking-normal text-muted-foreground/70">— always on for every role, nothing to grant</span>
              <InfoTip size={12} label="Infrastructure, not permissions: access is DERIVED from the role's feature grants (derive_service_perms), so disable a feature and its slice stops — the service itself never does." />
            </div>
            {SERVICES.map((sv) => (
              <div key={sv.id} className={rowCls()}>
                <div className="min-w-0">
                  <span className="text-sm font-medium">{sv.label}</span>
                  <div className="text-2xs text-muted-foreground/70">{sv.note}</div>
                </div>
                <div className="text-center">
                  <span
                    className="inline-flex items-center justify-center w-5 h-5 rounded bg-primary/40 text-foreground"
                    aria-label={`${sv.label} — always on for this role`}
                  >
                    <CheckMark />
                  </span>
                </div>
                {emptyCell}
                {/* A service has no Manage, but it CAN have config —
                    Alerts owns Group delivery. Three dashes here told the
                    owner there was nothing to grant, which is true of the
                    inbox and false of the delivery setup behind it. */}
                {configCellFor(sv.configVia, sv.configNote, 'can_manage_config_role')}
                {configCellFor(sv.configVia, sv.configNote, 'can_manage_config_all')}
              </div>
            ))}
          </>
        )}
        {!isDriver && GRID.bands.map((b) => (
          <div key={b.band}>
            <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              {b.band}
            </div>
            {b.families.map(famRow)}
          </div>
        ))}
        {/* Owner powers: not flags — is_primary_owner gates.  They are the
            ONLY real difference between Primary and Co-owner, so an owner
            weighing how much to trust a co-owner has to see them. */}
        {role === 'owner' && (
          <>
            <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              Owner powers <span className="normal-case tracking-normal text-muted-foreground/70">— primary owner only · not editable</span>
            </div>
            {api.ownerPowers.map((op) => (
              <div key={op.key} className={rowCls()}>
                <div className="min-w-0">
                  <span className="text-sm font-medium">{op.label}</span>
                  <div className="text-2xs text-muted-foreground/70">{op.description}</div>
                </div>
                <div className="text-center">
                  {seniorView
                    ? <span className="inline-flex items-center justify-center w-5 h-5 rounded bg-primary/40 text-foreground" aria-label="held"><CheckMark /></span>
                    : noflag}
                </div>
                {emptyCell}
                {emptyCell}
                {emptyCell}
              </div>
            ))}
          </>
        )}
        {!isDriver && (
          <div className="-mx-4 px-4 py-1 mt-1 bg-muted/40 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            Configuration
          </div>
        )}
        {/* The flags themselves — ONE row, because they are not features:
            the label spans the verb columns (no "– –" on rows that were
            never about View or Manage) and each tick sits under its own
            scope, in the same column as every feature that rides it.
            They still need a row of their own: can_manage_config_role
            governs page layouts, and the only page with layouts today is
            Alerts — an always-on service with no row to hang a cell on. */}
        {!isDriver && (
          <div className={rowCls()}>
            <div className="min-w-0 col-span-3">
              <span className="text-sm font-medium">Who may configure</span>
              <div className="text-2xs text-muted-foreground/70">
                the two flags above — one covers page layouts for their own role, the other a feature's shared settings
              </div>
            </div>
            {verbCell(capRow('can_manage_config_role'), 'config — own role')}
            {verbCell(capRow('can_manage_config_all'), 'config — account-wide')}
          </div>
        )}
        <p className="text-2xs text-muted-foreground mt-3">
          <span className="font-medium">–</span> means this feature has no flag of that verb —
          nothing to grant, not a denial.
          Where one flag covers both verbs, the Manage tick is drawn softer with a link
          mark (<Link2 className="inline align-[-2px] size-3" aria-hidden />) — either tick toggles both.
          * scoped feature — whose data is set per-user in Team Management.
        </p>
      </div>
    </div>
  );
}

// ── helpers ────────────────────────────────────────────────────────

const rowId = (r: TickRow): string => (isScoped(r) ? r.allKey : (r as { key: string }).key);
const capRow = (key: 'can_manage_config_all' | 'can_manage_config_role'): TickRow =>
  GRID.crossFeature.find((c) => rowId(c) === key)!;
const rowCls = (): string =>
  'grid grid-cols-[1fr_84px_84px_76px_84px] gap-x-2 items-center py-1.5 border-t border-border';

function DeltaChip() {
  return (
    <span className="ml-2 text-2xs font-semibold uppercase tracking-wide text-ok">
      manager adds
    </span>
  );
}
// The library already draws this. The hand-rolled 12x12 path it
// replaces was a second icon set of exactly one glyph.
function CheckMark() {
  return <Check className="size-3" strokeWidth={3} aria-hidden />;
}

// The view switcher's REAL contract (the one PersonaSelector uses):
// canSwitch gates who may preview; switchView(role) does it.
function useSafeRoleSwitch(): { canSwitchView: boolean; setRoleView: (r: string) => void } {
  const { canSwitch, switchView } = useRoleView();
  return { canSwitchView: canSwitch, setRoleView: switchView };
}
