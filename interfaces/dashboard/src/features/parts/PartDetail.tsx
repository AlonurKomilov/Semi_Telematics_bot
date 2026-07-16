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
import { ArrowLeft, Cog, Merge, Pencil, TriangleAlert } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
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
import type { AnyColumn, CatalogPart, PartAnalytics } from '../../types';

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

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', part_number: '', notes: '' });
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [busy, setBusy] = useState(false);

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
      render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
    },
    {
      key: 'total_quantity', label: 'Qty', sortable: true,
      render: (v) => <span className="tabular-nums">{Number(v ?? 0).toLocaleString()}</span>,
    },
    {
      key: 'total_spent', label: 'Spent', sortable: true,
      render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
    },
    {
      key: 'last_date', label: 'Last Service', sortable: true,
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
            className="text-left font-medium text-foreground hover:underline"
            onClick={(e) => { e.stopPropagation(); navigate(`/vendors/${r.vendor_id}`); }}
          >
            {String(v)}
          </button>
        ) : <span>{String(v)}</span>;
      },
    },
    {
      key: 'purchases', label: 'Purchases', sortable: true,
      render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
    },
    {
      key: 'avg_unit_price', label: 'Avg Price', sortable: true,
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums font-medium">{money(v, 2)}</span>),
    },
    {
      key: 'min_unit_price', label: 'Min', sortable: true,
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      key: 'max_unit_price', label: 'Max', sortable: true,
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      key: 'total_spent', label: 'Spent', sortable: true,
      render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
    },
    {
      key: 'last_date', label: 'Last Purchase', sortable: true,
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : <span className="text-muted-foreground">—</span>),
    },
  ], [tz, navigate]);

  const purchaseColumns: AnyColumn[] = useMemo(() => [
    {
      key: 'service_date', label: 'Date', sortable: true,
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : '—'),
    },
    { key: 'vehicle_name', label: 'Vehicle', sortable: true },
    { key: 'vendor_name', label: 'Vendor', sortable: true },
    {
      key: 'service_task', label: 'Task', sortable: false,
      render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'quantity', label: 'Qty', sortable: true,
      render: (v) => <span className="tabular-nums">{Number(v ?? 0).toLocaleString()}</span>,
    },
    {
      key: 'effective_unit_price', label: 'Unit Price', sortable: true,
      render: (v) => (v == null ? <span className="text-muted-foreground">—</span>
        : <span className="tabular-nums">{money(v, 2)}</span>),
    },
    {
      key: 'total_cost', label: 'Total', sortable: true,
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
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition mb-3"
      >
        <ArrowLeft size={14} /> Parts
      </button>

      <PageHeader
        icon={Cog}
        title={part?.name ?? 'Part'}
        description="Recurrence per vehicle, price per vendor, and the purchase history behind them. Void invoices never count."
        actions={part && (
          <span className="inline-flex items-center gap-2">
            {part.part_number && (
              <span className="font-mono text-xs text-muted-foreground border border-border rounded-md px-2 py-1">
                {part.part_number}
              </span>
            )}
            <Button variant="outline" size="sm" onClick={openEdit}>
              <Pencil size={14} /> Edit
            </Button>
            <Button variant="outline" size="sm" onClick={() => { setMergeTarget(''); setMergeOpen(true); }}>
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

      {/* Merge dialog — THIS part folds into the chosen survivor. */}
      <Dialog open={mergeOpen} onOpenChange={(o) => { if (!o) setMergeOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Merge “{part?.name}” into another part</DialogTitle>
            <DialogDescription>
              All of this part's invoice lines move to the part you pick,
              this record is deleted, and future synced lines under this
              name resolve to the survivor. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <Select value={mergeTarget} onValueChange={(v) => setMergeTarget(String(v))}>
            <SelectTrigger className="w-full" aria-label="Merge target"><SelectValue /></SelectTrigger>
            <SelectContent>
              {mergeTargets.map((p) => (
                <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setMergeOpen(false)}>Cancel</Button>
            <Button onClick={doMerge} disabled={!mergeTarget || busy}>
              {busy ? 'Merging…' : 'Merge'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
