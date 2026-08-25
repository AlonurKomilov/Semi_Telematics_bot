// Carrier Knowledge Base — list of the external carriers the recruiting team
// works with.  Read for every recruiter; a recruiting MANAGER (recruiter +
// is_manager, i.e. can_manage_carrier_directory) can add a carrier — which
// opens its profile to fill in.  Info-only (v1) — no links to the apply flow
// or any other feature.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, Building2, Check, Ban } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import PageHeader from '../../components/shell/PageHeader';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import DataGrid from '../../components/datagrid';
import type { DataGridSegment } from '../../components/datagrid';
import type { AnyColumn } from '../../types';
import EmptyState from '../../components/shell/EmptyState';
import ErrorState from '../../components/shell/ErrorState';
import { CardSkeleton } from '../../components/shell/LoadingSkeleton';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { useRoleView } from '../../context/RoleViewContext';
import { FIELD_COLUMNS, valueOf } from './fields';
import type { CarrierContent } from './fields';
import { intakeStateOf, INTAKE_LABEL, INTAKE_TONE, INTAKE_FILTER } from './intakeState';
import { Badge } from '@/components/ui/badge';

interface CarrierRow {
  id: number; name: string; website: string; experience_summary: string;
  intake_review_pending?: number | boolean;
  // The endpoint has always returned these; the list used to fetch and
  // drop them, so "invited three weeks ago, never replied" looked exactly
  // like "never invited".
  intake_expires_at?: string | null;
  intake_submitted_at?: string;
  intake_email?: string;
  intake_email_sent_at?: string;
  /** The carrier's profile answers, requested with ?fields=1 so any field
   *  can be put on screen as a column. */
  content?: CarrierContent;
}

/** Row fields the search box reaches that are NOT columns.  Every column
 *  is searched already, so this list stays short.  Module-level for
 *  stable identity: an inline array is a new one each render, which
 *  re-runs the grid's faceted filter-option pass over every row. */
const SEARCH_KEYS = ['name', 'experience_summary'];

/** Module-level for stable array identity across renders (DataGrid asks
 *  for this).  The grid computes the counts itself. */
// Ordered as the real pipeline a carrier travels — invited → filling →
// waiting on us → done — with the exception state last. Ordering them by
// stage is what makes a pile-up legible at a glance.
const SEGMENTS: DataGridSegment[] = [
  { key: 'all', label: 'All', showCount: false },
  {
    key: 'awaiting', label: 'Awaiting carrier',
    match: (r) => intakeStateOf(r as unknown as CarrierRow) === 'awaiting',
  },
  {
    key: 'review', label: 'Needs review',
    match: (r) => intakeStateOf(r as unknown as CarrierRow) === 'review',
  },
  {
    key: 'done', label: 'Filled in',
    match: (r) => intakeStateOf(r as unknown as CarrierRow) === 'done',
  },
  {
    key: 'lapsed', label: 'Lapsed',
    match: (r) => intakeStateOf(r as unknown as CarrierRow) === 'lapsed',
  },
];

export default function CarrierDirectory() {
  const { viewHasAny } = useRoleView();
  const canEdit = viewHasAny('can_manage_carrier_directory');
  const nav = useNavigate();
  const [rows, setRows] = useState<CarrierRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tz = useTimezone();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // ?fields=1 — the grid offers every profile field as an optional
      // column, so the content blob has to come with the list.
      const r = await apiJSON<{ items: CarrierRow[] }>('/carrier-directory/carriers?fields=1');
      setRows(r.items || []);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load carriers';
      // Held in state, not just a toast: a toast auto-dismisses and leaves
      // a failed load looking identical to an empty directory, with no way
      // back short of reloading the page.
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const createCarrier = async () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const created = await apiJSON<{ id: number }>('/carrier-directory/carriers', {
        method: 'POST', body: { name },
      });
      toast.success('Carrier added');
      nav(`/workforce/carrier-directory/${created.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add carrier');
    } finally {
      setBusy(false);
    }
  };

  const columns: AnyColumn[] = useMemo(() => [
    {
      key: 'name', label: 'Carrier', sortable: true, filterable: true,
      // A real <Link>, not just onRowClick: row-click alone gives the
      // primary action no focusable target, so the whole list was
      // mouse-only. The link is also what signals "this navigates" now
      // that the chevron is gone.
      render: (v: unknown, row: Record<string, unknown>) => (
        <Link
          to={`/workforce/carrier-directory/${Number(row.id)}`}
          onClick={(e) => e.stopPropagation()}
          className="font-medium text-foreground hover:underline"
        >
          {String(v || '')}
        </Link>
      ),
    },
    {
      // Was "Carrier updated", which reads as "someone edited this record"
      // — the ordinary meaning everywhere else — and hid the fact that an
      // action is required. Named from the reader's point of view now.
      key: 'intake_state', label: 'Fill link', sortable: true, filterable: true,
      filterMode: 'select',
      // Match on the CODE, display the label.  The row carries the
      // label (it's what sorts and exports), and without these a saved
      // tab would persist the literal "Needs review" as its criterion —
      // so the next time that wording changes, every stored tab scopes
      // to zero rows with no error.  This label has been reworded once
      // already (see above), which is exactly how it would happen.
      ...INTAKE_FILTER,
      render: (_v: unknown, row: Record<string, unknown>) => {
        const st = intakeStateOf(row as unknown as CarrierRow);
        if (st === 'none') return <span className="text-muted-foreground">—</span>;
        const expires = row.intake_expires_at ? String(row.intake_expires_at) : '';
        const soon = st === 'awaiting' && expires
          && new Date(expires).getTime() - Date.now() < 7 * 864e5;
        // ALWAYS state first, date second. The chip used to show a date
        // INSTEAD of the state inside the 7-day window, so two carriers in
        // the identical state rendered as different kinds of thing and
        // couldn't be compared down the column. Colour now encodes urgency
        // only — never identity.
        return (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <Badge tone={soon ? 'warn' : INTAKE_TONE[st]}>
              {INTAKE_LABEL[st]}
            </Badge>
            {expires && (st === 'awaiting' || st === 'lapsed') && (
              <span className="text-2xs text-muted-foreground">
                {st === 'lapsed' ? 'expired ' : 'expires '}
                {formatDay(expires, { timeZone: tz })}
              </span>
            )}
          </span>
        );
      },
    },
    {
      key: 'experience_summary', label: 'Accepted Experience Levels', sortable: true,
      render: (v: unknown) => (
        <span className="text-muted-foreground">{v ? String(v) : '—'}</span>
      ),
    },
    // Every profile field, available and hidden by default. This is the
    // whole point: a recruiter screening one driver against the book puts
    // the four or five fields they care about on screen — "pays over
    // $0.60, home weekly, takes pets" — and filters them together, which
    // no per-carrier detail page can do. Manage columns groups them by
    // section and has a search box, so 73 entries stay findable.
    ...FIELD_COLUMNS.map<AnyColumn>((fc) => ({
      key: fc.key,
      label: fc.def.label,
      group: fc.section.title,
      sortable: true,
      filterable: true,
      // A Yes/No answer has two values, so a picker beats a text box;
      // everything else is free prose and stays substring.
      filterMode: fc.def.type === 'bool' ? 'select' : undefined,
      // defaultHidden, not hidden: an operator who unhides a field keeps
      // it, and Reset puts it back. Their column choice is the saved view.
      defaultHidden: true,
      render: (v: unknown) => (v
        ? <span className="text-foreground">{String(v)}</span>
        : <span className="text-muted-foreground">—</span>),
    })),
  ], [tz]);

  // Precomputed so the column can sort/filter on it and the segments can
  // count it without re-deriving per cell. Field answers are flattened
  // onto the row for the same reason: DataGrid filters, sorts, searches
  // and exports ROW VALUES, so a render-only field column would display
  // correctly and filter on nothing.
  const gridRows = useMemo(
    () => rows.map((r) => {
      const flat: Record<string, unknown> = {
        ...r, intake_state: INTAKE_LABEL[intakeStateOf(r)],
      };
      for (const fc of FIELD_COLUMNS) {
        flat[fc.key] = valueOf(r.content, fc.section.key, fc.def);
      }
      return flat;
    }),
    [rows],
  );

  /** One bulk endpoint per action, each enforcing the same per-row rules
   *  the single-carrier action would — so the result is honestly partial
   *  ("3 done · 2 skipped") instead of silently pretending everything
   *  applied. Mirrors the applications bulk pattern. */
  const runBulk = useCallback(async (
    path: string, selected: Record<string, unknown>[], verb: string,
  ) => {
    const ids = selected.map((r) => Number(r.id)).filter(Boolean);
    if (!ids.length) return;
    try {
      const r = await apiJSON<{ updated: number; skipped: { reason: string }[] }>(
        `/carrier-directory/carriers/${path}`, { method: 'POST', body: { ids } },
      );
      const skipped = r.skipped?.length ?? 0;
      if (r.updated && skipped) {
        // Name WHY the rest were skipped — "2 skipped" alone reads as an error.
        const why = [...new Set(r.skipped.map((s) => s.reason))].join(', ');
        toast.warning(`${r.updated} ${verb} · ${skipped} skipped (${why})`);
      } else if (r.updated) {
        toast.success(`${r.updated} ${verb}`);
      } else {
        toast.info(`Nothing to do — ${skipped} skipped`);
      }
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Could not ${verb}`);
    }
  }, [load]);

  const bulkActions = useMemo(() => (canEdit ? [
    {
      label: 'Mark reviewed', icon: Check,
      onRun: (sel: Record<string, unknown>[]) =>
        runBulk('bulk-mark-reviewed', sel, 'marked reviewed'),
    },
    {
      label: 'Revoke links', icon: Ban, tone: 'danger' as const,
      // Only questioned at scale — confirming a single revoke twice (the
      // bar plus a prompt) trains people to click through prompts.
      confirm: (n: number) => n > 1
        ? `Revoke ${n} fill links? Any link already sent stops working. Answers carriers already submitted are kept.`
        : '',
      onRun: (sel: Record<string, unknown>[]) =>
        runBulk('bulk-revoke-link', sel, 'links revoked'),
    },
  ] : []), [canEdit, runBulk]);


  return (
    <div>
      <PageHeader
        title="Carrier Directory"
        icon={Building2}
        description="Reference info for the external carriers we recruit for — pre-qual criteria, presentation details and process notes."
        actions={canEdit && (adding ? (
          <div className="flex items-center gap-2">
            <Input autoFocus placeholder="Carrier name" value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') createCarrier();
                if (e.key === 'Escape') { setAdding(false); setNewName(''); }
              }} />
            <Button size="sm" onClick={createCarrier} disabled={busy || !newName.trim()}>Add</Button>
            <Button size="sm" variant="ghost" onClick={() => { setAdding(false); setNewName(''); }}>Cancel</Button>
          </div>
        ) : (
          <Button size="sm" onClick={() => setAdding(true)}><Plus /> Add carrier</Button>
        ))}
      />
      {/* Three distinct non-happy renders. They used to collapse into
          DataGrid's shared "No data", so a failed request and a brand-new
          account looked the same and neither explained itself. */}
      {loading ? (
        <CardSkeleton height="h-64" />
      ) : error && rows.length === 0 ? (
        <ErrorState message={error} onRetry={load} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No carriers yet"
          description="A carrier profile is your team's shared sheet on one external carrier you recruit for — who they will hire, what they pay, and how to submit drivers to them."
          action={canEdit ? (
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus /> Add your first carrier
            </Button>
          ) : (
            <p className="text-sm text-muted-foreground">
              A recruiting manager adds carriers to this directory.
            </p>
          )}
        />
      ) : (
        <DataGrid
          // A failed REFETCH keeps these rows up, so the
          // ``length === 0`` guard above never fires — the operator
          // reads a stale table as current.  The band says so without
          // removing the rows or the controls.
          error={error || undefined}
          onRetry={() => { void load(); }}
          columns={columns}
          data={gridRows as unknown as Record<string, unknown>[]}
          searchKey={SEARCH_KEYS}
          searchPlaceholder="Search carriers…"
          tableId="carrier-directory"
          segments={SEGMENTS}
          // Personal scope tabs on top of the lifecycle ones: the five
          // built-in stages are the pipeline, but the reason to hold 73
          // field columns is that a recruiter's own screen ("pays over
          // $0.60, home weekly, takes pets") is worth keeping. Managed
          // by right-click, per user.
          savedTabs
          // Deliberately not "at this stage" — a personal tab is a scope,
          // not a stage, and DataGrid shows this for any empty one.
          emptyMessage="No carrier here right now."
          bulkSelection={canEdit}
          bulkActions={bulkActions}
          // Without this every checkbox announces "Select row"; a screen
          // reader user picking three carriers out of forty needs the name.
          bulkRowLabel={(row) => `Select ${String(row.name ?? 'carrier')}`}
          onRowClick={(row) => nav(`/workforce/carrier-directory/${(row as unknown as CarrierRow).id}`)}
        />
      )}
    </div>
  );
}
