/**
 * Part profile — the drill-down the per-part cost report points at.
 *
 * Three lenses over invoice truth (void invoices and drafts never
 * count, same rule as every cost report):
 *   • recurrence per vehicle — the mean gap between service visits is
 *     the "this truck keeps eating this part" early-warning number;
 *   • price per vendor — what each shop charged for the same part;
 *   • purchase history — the price-trend source, row → work order.
 *
 * Edit (rename/part#/notes) and Merge (typo dedup) live here, same
 * contracts as the vendor profile.  Renames never rewrite invoice
 * snapshots.
 */
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  Tooltip as ChartTooltip, CartesianGrid,
} from 'recharts';
import { ArrowLeft, Check, Cog, Globe, History as HistoryIcon, Link2Off, Merge, Pencil, TriangleAlert } from 'lucide-react';
import { useAssemblies } from './useAssemblies';
import { ActivityTrailDialog } from '../../components/activity-trail/ActivityTrailDialog';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../components/ui/select';
import { Tip } from '../../components/tooltip';
import { chartColor, toneClasses } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import type { AnyColumn, CatalogPart, PartAnalytics, PublicPartEntry } from '../../types';

function money(v: unknown, digits = 0): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })}`;
}

/** A vehicle replacing the same part about every ≤45 days is the
 *  failing-component / bad-repair warning threshold. */
const FAST_REPEAT_DAYS = 45;

export default function PartDetail() {
  const { id } = useParams();
  const partId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tz = useTimezone();

  const { data, isLoading, error } = useQuery<PartAnalytics>({
    queryKey: ['part-detail', partId],
    queryFn: () => apiJSON<PartAnalytics>(`/parts/${partId}`),
    enabled: Number.isFinite(partId),
  });

  // Merge target roster — the rest of the catalog.
  const { data: catalogData } = useQuery<{ parts: CatalogPart[] }>({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON<{ parts: CatalogPart[] }>('/parts'),
  });
  const mergeTargets = (catalogData?.parts ?? []).filter((p) => p.id !== partId);

  const { groups: asmGroups, labelOf: asmLabelOf, all: asmAll } = useAssemblies();
  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', part_number: '', notes: '' });
  const [mergeOpen, setMergeOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [busy, setBusy] = useState(false);
  // Two dedup scopes, two verbs: 'mine' = destructive fold into
  // another of YOUR parts; 'public' = non-destructive LINK to the
  // canonical catalog entry (reversible — Unlink also suppresses
  // future auto-links so the user's call sticks).
  const [mergeScope, setMergeScope] = useState<'mine' | 'public'>('mine');
  const [pubTerm, setPubTerm] = useState('');
  const [pubPick, setPubPick] = useState<PublicPartEntry | null>(null);

  const { data: pubSearch } = useQuery<{ entries: PublicPartEntry[] }>({
    queryKey: ['parts-public-search', pubTerm],
    queryFn: () => apiJSON(`/parts/public/browse?q=${encodeURIComponent(pubTerm)}`),
    enabled: mergeOpen && mergeScope === 'public' && pubTerm.trim().length >= 2,
    staleTime: 30_000,
  });

  const part = data?.part;

  const totals = useMemo(() => {
    const purchases = data?.purchases ?? [];
    const spent = (data?.by_vehicle ?? []).reduce((s, v) => s + Number(v.total_spent || 0), 0);
    const prices = purchases
      .map((p) => p.effective_unit_price)
      .filter((v): v is number => v != null);
    return {
      uses: purchases.length,
      spent,
      vehicles: (data?.by_vehicle ?? []).length,
      avgPrice: prices.length
        ? prices.reduce((s, v) => s + v, 0) / prices.length
        : null,
    };
  }, [data]);

  // Chronological price points for the trend line.
  const trend = useMemo(() => (
    (data?.purchases ?? [])
      .filter((p) => p.effective_unit_price != null && p.service_date)
      .slice()
      .reverse()
      .map((p) => ({
        date: formatDay(p.service_date, { timeZone: tz }),
        price: p.effective_unit_price as number,
        vendor: p.vendor_name,
      }))
  ), [data, tz]);

  const vehicleColumns: AnyColumn[] = useMemo(() => [
    { key: 'vehicle_name', label: 'Vehicle', sortable: true },
    {
      key: 'usage_count', label: 'Uses', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
    },
    {
      key: 'total_quantity', label: 'Qty', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      aggFormat: (value) => value.toLocaleString(undefined, { maximumFractionDigits: 2 }),
      render: (v) => <span className="tabular-nums">{Number(v ?? 0).toLocaleString()}</span>,
    },
    {
      key: 'total_spent', label: 'Spent', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      aggFormat: (value) => money(value),
      render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
    },
    {
      key: 'last_date', label: 'Last Service', sortable: true,
      // Most-recent service across vehicles (max) / earliest (min).
      aggregable: true, aggType: 'date',
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'avg_interval_days', label: 'Avg Interval', sortable: true,
      render: (v) => {
        if (v == null) return <span className="text-muted-foreground">—</span>;
        const days = Number(v);
        const fast = days <= FAST_REPEAT_DAYS;
        return (
          <span className="inline-flex items-center gap-1.5">
            <span className="tabular-nums">{days} d</span>
            {fast && (
              <Tip label={`Replaced about every ${days} days — worth checking for a failing component or a repair that isn't holding.`}>
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border text-2xs font-medium ${toneClasses('warn')}`}>
                  <TriangleAlert size={12} /> repeating fast
                </span>
              </Tip>
            )}
          </span>
        );
      },
    },
  ], [tz]);

  const vendorColumns: AnyColumn[] = useMemo(() => [
    {
      key: 'vendor_name', label: 'Vendor', sortable: true,
      render: (v, row) => {
        const r = row as unknown as PartAnalytics['by_vendor'][number];
        return r.vendor_id ? (
          <button
            type="button"
            className="text-left font-medium text-foreground hover:underline py-0.5 -my-0.5 min-h-tap"
            onClick={(e) => { e.stopPropagation(); navigate(`/vendors/${r.vendor_id}`); }}
          >
            {String(v)}
          </button>
        ) : <span>{String(v)}</span>;
      },
    },
    {
      key: 'purchases', label: 'Purchases', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
    },
    {
      // NOT aggregable — a per-vendor AVERAGE; averaging averages is
      // unweighted/misleading (blended price = sum(spent)/sum(qty)).
      key: 'avg_unit_price', label: 'Avg Price', sortable: true,
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums font-medium">{money(v, 2)}</span>),
    },
    {
      // min-of-mins = the true lowest price any vendor charged.
      key: 'min_unit_price', label: 'Min', sortable: true,
      aggregable: true, aggFns: ['min'],
      aggFormat: (value) => money(value, 2),
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      // max-of-maxes = the true highest price paid.
      key: 'max_unit_price', label: 'Max', sortable: true,
      aggregable: true, aggFns: ['max'],
      aggFormat: (value) => money(value, 2),
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      key: 'total_spent', label: 'Spent', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      aggFormat: (value) => money(value),
      render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
    },
    {
      key: 'last_date', label: 'Last Purchase', sortable: true,
      aggregable: true, aggType: 'date',
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : <span className="text-muted-foreground">—</span>),
    },
  ], [tz, navigate]);

  const purchaseColumns: AnyColumn[] = useMemo(() => [
    {
      key: 'service_date', label: 'Date', sortable: true,
      // Earliest / latest purchase line in the filtered (or grouped) set.
      aggregable: true, aggType: 'date',
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : '—'),
    },
    { key: 'vehicle_name', label: 'Vehicle', sortable: true, pivotable: true },
    { key: 'vendor_name', label: 'Vendor', sortable: true, pivotable: true },
    {
      key: 'service_task', label: 'Service task', sortable: false, pivotable: true,
      render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'quantity', label: 'Qty', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      aggFormat: (value) => value.toLocaleString(undefined, { maximumFractionDigits: 2 }),
      render: (v) => <span className="tabular-nums">{Number(v ?? 0).toLocaleString()}</span>,
    },
    {
      // Per-line unit price is a RATIO — avg/min/max, never sum.
      key: 'effective_unit_price', label: 'Unit Price', sortable: true,
      aggregable: true, aggFns: ['avg', 'min', 'max'],
      aggFormat: (value) => money(value, 2),
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      key: 'total_cost', label: 'Total', sortable: true,
      aggregable: true, aggFns: ['sum', 'avg', 'max'],
      aggFormat: (value) => money(value, 2),
      render: (v) => <span className="tabular-nums font-medium">{money(v, 2)}</span>,
    },
  ], [tz]);

  const openEdit = () => {
    if (!part) return;
    setEditForm({
      name: part.name,
      part_number: part.part_number ?? '',
      notes: part.notes ?? '',
    });
    setEditOpen(true);
  };

  const saveEdit = async () => {
    if (!editForm.name.trim()) { toast.error('Name is required'); return; }
    setBusy(true);
    try {
      await apiJSON(`/parts/${partId}`, {
        method: 'PUT',
        body: JSON.stringify(editForm),
      });
      toast.success('Part updated');
      setEditOpen(false);
      qc.invalidateQueries({ queryKey: ['part-detail', partId] });
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
    } catch (e) {
      // 409 = name collision with another part → the fix is a merge.
      toast.error(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setBusy(false);
    }
  };

  const setAssembly = async (key: string) => {
    setBusy(true);
    try {
      await apiJSON(`/parts/${partId}`, {
        method: 'PUT', body: { assembly_key: key },
      });
      toast.success(key ? 'Assembly set' : 'Assembly cleared');
      qc.invalidateQueries({ queryKey: ['part-detail', partId] });
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  };

  const doMerge = async () => {
    if (!mergeTarget) return;
    setBusy(true);
    try {
      // THIS part is the loser: "merge this page's part into X".
      await apiJSON(`/parts/${partId}/merge-into/${mergeTarget}`, { method: 'POST' });
      toast.success('Parts merged');
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
      navigate(`/parts/${mergeTarget}`, { replace: true });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Merge failed');
    } finally {
      setBusy(false);
      setMergeOpen(false);
    }
  };

  const doLink = async () => {
    if (!pubPick) return;
    setBusy(true);
    try {
      // Non-destructive: the row + its invoice lines stay; an empty
      // part number fills from the canonical entry.
      await apiJSON(`/parts/${partId}/link-public/${pubPick.id}`, { method: 'POST' });
      toast.success('Linked to the catalog — nothing was deleted. Unlink here reverses it.');
      qc.invalidateQueries({ queryKey: ['part-detail', partId] });
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
      setMergeOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Link failed');
    } finally {
      setBusy(false);
    }
  };

  const doUnlink = async () => {
    setBusy(true);
    try {
      await apiJSON(`/parts/${partId}/link-public`, { method: 'DELETE' });
      toast.success('Unlinked — this part will not be auto-linked again unless you re-link it.');
      qc.invalidateQueries({ queryKey: ['part-detail', partId] });
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Unlink failed');
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="p-4 md:p-6">
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load part'} />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6">
      <button
        type="button"
        onClick={() => navigate('/parts')}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition mb-3 py-1 -my-1 min-h-tap"
      >
        <ArrowLeft size={14} /> Parts
      </button>

      <PageHeader
        icon={Cog}
        title={part?.name ?? 'Part'}
        description="Recurrence per vehicle, price per vendor, and the purchase history behind them. Void invoices never count."
        actions={part && (
          <span className="inline-flex items-center gap-2">
            {/* Stable identifier for merge bookkeeping/support — a
                global serial, not a per-account sequence. */}
            <Tip label="Part ID — use it to be sure which record you're merging or referencing.">
              <span className="font-mono text-xs text-muted-foreground border border-border rounded-md px-2 py-1">
                #{part.id}
              </span>
            </Tip>
            {part.part_number && (
              <span className="font-mono text-xs text-muted-foreground border border-border rounded-md px-2 py-1">
                {part.part_number}
              </span>
            )}
            <Button variant="outline" size="sm" onClick={openEdit}>
              <Pencil size={14} /> Edit
            </Button>
            <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)}>
              <HistoryIcon size={14} /> History
            </Button>
            <Button variant="outline" size="sm" onClick={() => {
              setMergeScope('mine'); setMergeTarget('');
              setPubTerm(''); setPubPick(null);
              setMergeOpen(true);
            }}>
              <Merge size={14} /> Merge into…
            </Button>
          </span>
        )}
      />

      {isLoading || !data ? (
        <TableSkeleton rows={8} cols={5} />
      ) : (
        <>
          {/* Headline numbers */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {[
              { label: 'Purchases', value: String(totals.uses) },
              { label: 'Total Spent', value: money(totals.spent) },
              { label: 'Vehicles', value: String(totals.vehicles) },
              { label: 'Avg Unit Price', value: totals.avgPrice == null ? '—' : money(totals.avgPrice, 2) },
            ].map((s) => (
              <div key={s.label} className="bg-card border border-border rounded-lg p-3">
                <p className="text-xs text-muted-foreground">{s.label}</p>
                <p className="text-xl font-bold tabular-nums text-foreground">{s.value}</p>
              </div>
            ))}
          </div>

          {/* Classification — the approved mock: the whole ladder in
              one line.  System is never picked; it DERIVES from the
              chosen assembly, and saying so is the lesson.  Sits as
              the sibling of the catalog strip on purpose: that strip
              answers "which product is this?" (identity), this one
              "what kind of thing is this?" (classification). */}
          <div className="bg-card border border-border rounded-lg p-3 mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <Cog size={16} className="text-muted-foreground shrink-0" />
            <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">System</span>
            {(() => {
              const asm = asmAll.find((a) => a.key === (data.part.assembly_key ?? ''));
              const sysLabel = asm
                ? asmGroups.find((g) => g.systemKey === asm.system_key)?.systemLabel ?? asm.system_key
                : null;
              return sysLabel ? (
                <span className="text-sm text-foreground">
                  {sysLabel}
                  <span className="ml-1.5 text-2xs text-muted-foreground">from the assembly</span>
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">Unassigned</span>
              );
            })()}
            <span className="text-muted-foreground/60">→</span>
            <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">Assembly</span>
            <select
              value={data.part.assembly_key ?? ''}
              disabled={busy}
              onChange={(e) => setAssembly(e.target.value)}
              aria-label="Assembly"
              className="h-8 bg-muted border border-border rounded-md px-2 text-sm text-foreground focus:outline-none focus:border-ring"
            >
              <option value="">— No assembly —</option>
              {asmGroups.map((g) => (
                <optgroup key={g.systemKey} label={g.systemLabel}>
                  {g.items.map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            {!data.part.assembly_key && data.part.suggested_assembly && (
              <button
                type="button"
                disabled={busy}
                onClick={() => setAssembly(data.part.suggested_assembly!)}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs font-medium transition hover:brightness-110 ${toneClasses('info')} min-h-tap`}
              >
                <Check size={12} aria-hidden />
                {asmLabelOf.get(data.part.suggested_assembly) ?? data.part.suggested_assembly}?
              </button>
            )}
            <span className="text-muted-foreground/60">→</span>
            <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">Part</span>
            <span className="text-sm font-medium text-foreground min-w-0 truncate">{data.part.name}</span>
          </div>

          {/* Public-catalog link state: linking is automatic by name
              (or via Merge into… → Catalog); Unlink is the
              wrong-match escape hatch and it STICKS. */}
          <div className="bg-card border border-border rounded-lg p-3 mb-4 flex flex-wrap items-center gap-3">
            <Globe size={16} className="text-muted-foreground shrink-0" />
            {data.public ? (
              <>
                <p className="text-sm text-foreground min-w-0">
                  In the catalog as <span className="font-medium">{data.public.name}</span>
                  {data.public.category && (
                    <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded-full border border-border text-2xs text-muted-foreground">
                      {data.public.category}
                    </span>
                  )}
                </p>
                <button type="button" onClick={doUnlink} disabled={busy}
                  className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-muted-foreground hover:text-foreground transition min-h-tap">
                  <Link2Off size={14} /> Unlink
                </button>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Not in the catalog — parts link automatically when
                their name matches a curated entry, or via Merge into… →
                Catalog.
              </p>
            )}
          </div>

          {/* Geographic market estimates — "what should this part
              cost around me?"  Hidden entirely while the platform
              switch is off or the part isn't catalog-linked; the
              give-to-get pitch shows the count when not sharing. */}
          {data.market && data.market.reason !== 'disabled' && data.market.reason !== 'not_linked' && (
            <div className="bg-card border border-border rounded-lg p-3 mb-4">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
                Estimated market price
              </p>
              {data.market.available ? (
                (data.market.national || (data.market.states ?? []).length > 0) ? (
                  <div className="flex flex-wrap items-center gap-2">
                    {data.market.national && (
                      <Tip label={`Typical range across ${data.market.national.companies} companies · ${data.market.national.invoices} invoices · last ${data.market.national.window_months} months. Prices vary by volume and situation.`}>
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-border text-sm text-foreground">
                          <span className="text-xs text-muted-foreground">National</span>
                          <span className="tabular-nums font-medium">
                            {money(data.market.national.p25, 0)}–{money(data.market.national.p75, 0)}
                          </span>
                        </span>
                      </Tip>
                    )}
                    {(data.market.states ?? []).map((s) => (
                      <Tip key={s.region} label={`${s.companies} companies · ${s.invoices} invoices · last ${s.window_months} months.`}>
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-border text-sm text-foreground">
                          <span className="text-xs text-muted-foreground">{s.region}</span>
                          <span className="tabular-nums font-medium">
                            {money(s.p25, 0)}–{money(s.p75, 0)}
                          </span>
                        </span>
                      </Tip>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Not enough data yet — ranges publish once 3+ companies
                    have bought this part.
                  </p>
                )
              ) : (
                <p className="text-sm text-muted-foreground">
                  {(data.market.available_count ?? 0) > 0
                    ? `${data.market.available_count} market range${(data.market.available_count ?? 0) > 1 ? 's' : ''} available for this part — enable market sharing in Settings to see them (your data contributes anonymously in return).`
                    : 'Enable market sharing in Settings to see typical prices here as they become available (your data contributes anonymously in return).'}
                </p>
              )}
            </div>
          )}

          {data.purchases.length === 0 ? (
            <EmptyState
              icon={Cog}
              title="No purchases recorded yet"
              description="This part has no dated, non-void work-order lines. Analytics appear with its first real invoice."
            />
          ) : (
            <div className="flex flex-col gap-4">
              {/* Recurrence per vehicle */}
              <section>
                <h2 className="text-lg font-semibold text-foreground mb-2">By vehicle</h2>
                <DataGrid
                  tableId="part-by-vehicle"
                  columns={vehicleColumns}
                  data={data.by_vehicle as unknown as Record<string, unknown>[]}
                  enableToolbar={false}
                  enablePagination={false}
                />
              </section>

              {/* Price per vendor */}
              <section>
                <h2 className="text-lg font-semibold text-foreground mb-2">By vendor</h2>
                <DataGrid
                  tableId="part-by-vendor"
                  columns={vendorColumns}
                  data={data.by_vendor as unknown as Record<string, unknown>[]}
                  enableToolbar={false}
                  enablePagination={false}
                />
              </section>

              {/* Price trend — needs at least two priced purchases to be a line */}
              {trend.length >= 2 && (
                <section>
                  <h2 className="text-lg font-semibold text-foreground mb-2">Unit price over time</h2>
                  <div className="bg-card border border-border rounded-lg p-3 h-56">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)"
                          tickFormatter={(v: number) => `$${v}`} width={52} />
                        <ChartTooltip
                          formatter={(value: unknown, _n: unknown, entry: { payload?: { vendor?: string } }) => [
                            money(Number(value), 2),
                            entry?.payload?.vendor ?? 'Unit price',
                          ]}
                          contentStyle={{
                            background: 'var(--card)', border: '1px solid var(--border)',
                            borderRadius: 'var(--radius)', color: 'var(--foreground)', fontSize: 12,
                          }}
                        />
                        <Line type="monotone" dataKey="price" stroke={chartColor(0)}
                          strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </section>
              )}

              {/* Purchase history */}
              <section>
                <h2 className="text-lg font-semibold text-foreground mb-2">Purchase history</h2>
                <DataGrid
                  tableId="part-purchases"
                  // "What did this part cost us, by vendor, by month?" —
                  // the purchase LINES are transactional, so pivoting them
                  // is honest.  The by-vehicle / by-vendor grids above are
                  // already aggregates and stay un-pivoted.
                  pivot
                  columns={purchaseColumns}
                  data={data.purchases as unknown as Record<string, unknown>[]}
                  onRowClick={(row) => {
                    const p = row as unknown as PartAnalytics['purchases'][number];
                    navigate(`/work-orders/${p.work_order_id}`);
                  }}
                />
              </section>
            </div>
          )}
        </>
      )}

      {/* Edit dialog — invoice snapshots stay untouched by renames. */}
      <Dialog open={editOpen} onOpenChange={(o) => { if (!o) setEditOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Edit part</DialogTitle>
            <DialogDescription>
              Renaming updates the catalog only — line items on past
              invoices keep the name they were saved with.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2.5">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-edit-name">Name</label>
            <input
              id="part-edit-name"
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              value={editForm.name}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
            />
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-edit-number">Part number</label>
            <input
              id="part-edit-number"
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground font-mono"
              value={editForm.part_number}
              onChange={(e) => setEditForm((f) => ({ ...f, part_number: e.target.value }))}
            />
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-edit-notes">Notes</label>
            <textarea
              id="part-edit-notes"
              rows={3}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
              value={editForm.notes}
              onChange={(e) => setEditForm((f) => ({ ...f, notes: e.target.value }))}
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>Cancel</Button>
            <Button onClick={saveEdit} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Merge dialog — the same My-vs-Shared binary as the page tabs,
          so the same two words.  "My parts" folds THIS part into the
          chosen survivor (destructive); "Shared" LINKS it to the
          canonical catalog entry (non-destructive, reversible via
          Unlink). */}
      <Dialog open={mergeOpen} onOpenChange={(o) => { if (!o) setMergeOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Resolve “{part?.name}” as a duplicate</DialogTitle>
            <DialogDescription>
              {mergeScope === 'mine'
                ? "All of this part's invoice lines move to the part you pick, this record is deleted, and future synced lines under this name resolve to the survivor. This cannot be undone."
                : 'This part is the same thing as a catalog entry. Linking keeps this record and its invoice lines — an empty part number fills from the canonical entry. Reversible with Unlink.'}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-1 rounded-md border border-border p-1 bg-muted/40" role="tablist" aria-label="Duplicate scope">
            {([['mine', 'My parts'], ['public', 'Shared']] as const).map(([scope, label]) => (
              <button
                key={scope}
                type="button"
                role="tab"
                aria-selected={mergeScope === scope}
                onClick={() => setMergeScope(scope)}
                className={`flex-1 h-8 rounded text-sm font-medium transition ${
                  mergeScope === scope
                    ? 'bg-card text-foreground border border-border shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {mergeScope === 'mine' ? (
            <Select value={mergeTarget} onValueChange={(v) => setMergeTarget(String(v))}>
              <SelectTrigger className="w-full" aria-label="Merge target"><SelectValue /></SelectTrigger>
              <SelectContent>
                {mergeTargets.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    <span className="font-mono text-xs text-muted-foreground">#{p.id}</span> {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="flex flex-col gap-2">
              <input
                className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
                placeholder="Search the catalog…"
                value={pubTerm}
                onChange={(e) => { setPubTerm(e.target.value); setPubPick(null); }}
              />
              {pubTerm.trim().length >= 2 && (
                <div className="max-h-48 overflow-y-auto rounded-md border border-border divide-y divide-border">
                  {(pubSearch?.entries ?? []).length === 0 ? (
                    <p className="p-2.5 text-sm text-muted-foreground">No matching catalog entries.</p>
                  ) : (pubSearch?.entries ?? []).map((e) => (
                    <button
                      key={e.id}
                      type="button"
                      onClick={() => setPubPick(e)}
                      className={`w-full text-left p-2.5 text-sm transition ${
                        pubPick?.id === e.id ? 'bg-primary/10 text-foreground' : 'text-foreground hover:bg-muted'
                      }`}
                    >
                      <span className="font-medium">{e.name}</span>
                      {e.category ? <span className="ml-2 text-2xs text-muted-foreground">{e.category}</span> : null}
                      {e.description && <span className="block text-xs text-muted-foreground truncate">{e.description}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setMergeOpen(false)}>Cancel</Button>
            {mergeScope === 'mine' ? (
              <Button onClick={doMerge} disabled={!mergeTarget || busy}>
                {busy ? 'Merging…' : 'Merge'}
              </Button>
            ) : (
              <Button onClick={doLink} disabled={!pubPick || busy}>
                {busy ? 'Linking…' : 'Link'}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ActivityTrailDialog
        entityType="part"
        entityId={part?.id ?? null}
        title={`${part?.name ?? 'Part'} — activity history`}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />
    </div>
  );
}
