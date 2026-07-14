/**
 * VendorProfile — one shop's page: contact card, spend rollups, the
 * full work-order history ("every part bought, at what price, with
 * what labor" — parts/labor detail is one click into each WO), and
 * the merge tool for typo-duplicates.
 */
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Store, ArrowLeft, Merge, Globe, Link2, Link2Off, Send, Star, TrendingUp } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { PageHeader, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../components/ui/select';
import { Button } from '../../components/ui/button';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { TASK_TYPE_OPTIONS } from '../maintenance/badges';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { toneClasses, type Tone } from '../../lib/status';
import type { Vendor, WorkOrder, AnyColumn, DirectoryEntry, MarketRollupRow } from '../../types';

const PAYMENT_TONE: Record<string, Tone> = {
  unpaid: 'warn', paid: 'ok', partial: 'warn', void: 'neutral',
};

function money(v: unknown, digits = 2): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export default function VendorProfile() {
  const { id } = useParams<{ id: string }>();
  const vendorId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tz = useTimezone();
  const { has } = useViewPermissions();
  const canWrite = has('can_work_orders_all');
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [merging, setMerging] = useState(false);

  const { data, isLoading, error } = useQuery<{
    vendor: Vendor; work_orders: WorkOrder[]; directory: DirectoryEntry | null;
  }>({
    queryKey: ['vendor', vendorId],
    queryFn: () => apiJSON(`/vendors/${vendorId}`),
    enabled: Number.isFinite(vendorId),
  });
  // Global-directory link state + actions (Phase C).
  const [linkOpen, setLinkOpen] = useState(false);
  const [dirQuery, setDirQuery] = useState('');
  const [dirBusy, setDirBusy] = useState(false);
  const { data: dirResults } = useQuery<{ entries: DirectoryEntry[] }>({
    queryKey: ['directory-search', dirQuery],
    queryFn: () => apiJSON(`/vendors/directory/search?q=${encodeURIComponent(dirQuery)}`),
    enabled: linkOpen,
  });
  const dirAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    setDirBusy(true);
    try {
      await fn();
      toast.success(okMsg);
      qc.invalidateQueries({ queryKey: ['vendor', vendorId] });
      setLinkOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    } finally { setDirBusy(false); }
  };
  const suggestToDirectory = () => dirAction(async () => {
    const r = await apiJSON<{ status: string; linked: boolean }>(
      `/vendors/${vendorId}/suggest-to-directory`, { method: 'POST' });
    if (r.status === 'pending') {
      toast.info('Suggested — pending platform review.');
    }
  }, 'Sent to the global directory');
  const linkTo = (entryId: number) => dirAction(
    () => apiJSON(`/vendors/${vendorId}/link-directory/${entryId}`, { method: 'POST' }),
    'Linked to directory');
  const unlink = () => dirAction(
    () => apiJSON(`/vendors/${vendorId}/link-directory`, { method: 'DELETE' }),
    'Unlinked');
  // Rate dialog (anonymous stars/comment, moderated platform-side).
  const [rateOpen, setRateOpen] = useState(false);
  const [rateStars, setRateStars] = useState(5);
  const [rateComment, setRateComment] = useState('');
  const submitReview = () => dirAction(async () => {
    await apiJSON(`/vendors/directory/${data!.directory!.id}/review`, {
      method: 'POST', body: { rating: rateStars, comment: rateComment },
    });
    setRateOpen(false);
  }, 'Review submitted — pending moderation');
  // Market intelligence (Phase D): anonymized typical price ranges
  // for the linked directory shop.  Triple-gated server-side (flag +
  // access + give-to-get); the reason tells us which pitch to show.
  const canManageAccount = has('can_manage_account');
  const dirId = data?.directory?.id;
  const { data: market } = useQuery<{ available: boolean; reason: string; rows: MarketRollupRow[] }>({
    queryKey: ['vendor-market', dirId],
    queryFn: () => apiJSON(`/vendors/directory/${dirId}/market`),
    enabled: !!dirId,
  });
  const enableSharing = () => dirAction(async () => {
    await apiJSON('/vendors/market-sharing', { method: 'PUT', body: { enabled: true } });
    qc.invalidateQueries({ queryKey: ['vendor-market'] });
  }, 'Market data sharing enabled');
  const marketLabel = (r: MarketRollupRow): string => {
    if (r.dim_type === 'part') return r.dim_label || r.dim_key;
    const opt = TASK_TYPE_OPTIONS.find(o => o.value === r.dim_key);
    return opt?.label ?? r.dim_key.replace(/^custom_/, '').replace(/[_-]+/g, ' ')
      .replace(/\b\w/g, (c: string) => c.toUpperCase());
  };

  // Roster for the merge target picker.
  const { data: allData } = useQuery<{ vendors: Vendor[] }>({
    queryKey: ['vendors'],
    queryFn: () => apiJSON<{ vendors: Vendor[] }>('/vendors'),
    staleTime: 60_000,
  });
  const others = useMemo(
    () => (allData?.vendors ?? []).filter(v => v.id !== vendorId),
    [allData, vendorId],
  );

  const vendor = data?.vendor;
  const workOrders = useMemo(() => data?.work_orders ?? [], [data]);
  const totals = useMemo(() => ({
    count: workOrders.length,
    spent: workOrders.reduce((a, w) => a + (w.total_cost ?? 0), 0),
    unpaid: workOrders
      .filter(w => w.payment_status === 'unpaid' && w.status !== 'void')
      .reduce((a, w) => a + (w.total_cost ?? 0), 0),
  }), [workOrders]);

  const doMerge = async () => {
    if (!mergeTarget) return;
    setMerging(true);
    try {
      // THIS vendor is the loser: "merge this page's vendor into X".
      await apiJSON(`/vendors/${vendorId}/merge-into/${mergeTarget}`, { method: 'POST' });
      toast.success('Vendors merged');
      qc.invalidateQueries({ queryKey: ['vendors'] });
      qc.invalidateQueries({ queryKey: ['work-orders'] });
      navigate(`/vendors/${mergeTarget}`, { replace: true });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Merge failed');
    } finally {
      setMerging(false);
      setMergeOpen(false);
    }
  };

  const columns: AnyColumn[] = [
    { key: 'id', label: '#', sortable: true,
      render: (v) => <span className="font-mono text-xs text-muted-foreground">{`#${v}`}</span> },
    { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
    { key: 'service_date', label: 'Service Date', sortable: true,
      filterable: true, filterMode: 'date-range',
      render: (v) => (v ? formatDay(String(v), { timeZone: tz }) : <span className="text-muted-foreground">—</span>) },
    { key: 'invoice_number', label: 'Invoice #',
      render: (v) => (v ? <span className="font-mono text-xs">{String(v)}</span> : <span className="text-muted-foreground">—</span>) },
    { key: 'total_cost', label: 'Total', sortable: true,
      render: (v) => <span className="tabular-nums font-medium">{money(v)}</span> },
    { key: 'payment_status', label: 'Payment', sortable: true, filterable: true,
      render: (v) => {
        const s = String(v || '').toLowerCase();
        return (
          <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium capitalize ${toneClasses(PAYMENT_TONE[s] ?? 'neutral')}`}>
            {s || '—'}
          </span>
        );
      } },
  ];

  if (error instanceof Error) {
    return (
      <div>
        <PageHeader icon={Store} title="Vendor" description="" />
        <ErrorState message={error.message} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={Store}
        title={vendor?.name ?? 'Vendor'}
        description="Registry record — spend history rolls up from this vendor's work orders."
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/vendors')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded-md text-xs font-medium text-foreground transition"
            >
              <ArrowLeft size={14} />
              All vendors
            </button>
            {canWrite && others.length > 0 && (
              <button
                type="button"
                onClick={() => setMergeOpen(true)}
                title="Fold this vendor into another (fixes typo-duplicates); its work orders move over."
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded-md text-xs font-medium text-foreground transition"
              >
                <Merge size={14} />
                Merge into…
              </button>
            )}
          </div>
        }
      />

      {isLoading && !vendor ? (
        <TableSkeleton rows={5} cols={5} />
      ) : vendor && (
        <>
          {/* Contact + rollups */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="bg-card border border-border rounded-lg p-3">
              <p className="text-xs text-muted-foreground">Contact</p>
              <p className="text-sm mt-1">{vendor.phone || '—'}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{vendor.email || ''}</p>
            </div>
            <div className="bg-card border border-border rounded-lg p-3">
              <p className="text-xs text-muted-foreground">Address</p>
              <p className="text-sm mt-1">{vendor.address || '—'}</p>
            </div>
            <div className="bg-card border border-border rounded-lg p-3">
              <p className="text-xs text-muted-foreground">Work Orders</p>
              <p className="text-xl font-bold tabular-nums">{totals.count}</p>
            </div>
            <div className="bg-card border border-border rounded-lg p-3">
              <p className="text-xs text-muted-foreground">Total Spent</p>
              <p className="text-xl font-bold tabular-nums">{money(totals.spent, 0)}</p>
              {totals.unpaid > 0 && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  {money(totals.unpaid, 0)} unpaid
                </p>
              )}
            </div>
          </div>

          {/* Global directory link (Phase C): identity handshake only —
              linking shares nothing; suggesting shares ONLY the shop's
              name/contact for operator review. */}
          <div className="bg-card border border-border rounded-lg p-3 mb-4 flex flex-wrap items-center gap-3">
            <Globe size={16} className="text-muted-foreground shrink-0" />
            {data?.directory ? (
              <>
                <div className="min-w-0">
                  <p className="text-sm text-foreground">
                    In global directory as <span className="font-medium">{data.directory.name}</span>
                    {(data.directory.rating_count ?? 0) > 0 && (
                      <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Star size={12} className="text-warn" />
                        <span className="tabular-nums">{data.directory.rating_avg}</span>
                        <span>({data.directory.rating_count})</span>
                      </span>
                    )}
                  </p>
                  {data.directory.services && (
                    <p className="text-xs text-muted-foreground mt-0.5">{data.directory.services}</p>
                  )}
                  {data.directory.my_review?.status === 'pending' && (
                    <p className="text-xs text-muted-foreground mt-0.5">Your review is pending moderation.</p>
                  )}
                </div>
                {canWrite && (
                  <span className="ml-auto inline-flex items-center gap-2">
                    <button type="button" onClick={() => {
                      setRateStars(data.directory?.my_review?.rating ?? 5);
                      setRateComment(data.directory?.my_review?.comment ?? '');
                      setRateOpen(true);
                    }} disabled={dirBusy}
                      title="Anonymous rating — displayed with no company attribution, after platform moderation."
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-foreground transition">
                      <Star size={13} /> {data.directory.my_review ? 'Edit review' : 'Rate this shop'}
                    </button>
                    <button type="button" onClick={unlink} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-muted-foreground hover:text-foreground transition">
                      <Link2Off size={13} /> Unlink
                    </button>
                  </span>
                )}
              </>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">Not linked to the global directory.</p>
                {canWrite && (
                  <span className="ml-auto inline-flex items-center gap-2">
                    <button type="button" onClick={() => setLinkOpen(true)} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-foreground transition">
                      <Link2 size={13} /> Link to directory…
                    </button>
                    <button type="button" onClick={suggestToDirectory} disabled={dirBusy}
                      title="Share only this shop's name and contact info for platform review — never your invoices or spend."
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-foreground transition">
                      <Send size={13} /> Suggest to directory
                    </button>
                  </span>
                )}
              </>
            )}
          </div>

          {/* Market ranges (Phase D) — renders only when the platform
              flag is live.  'not_sharing' shows the give-to-get pitch;
              'disabled' renders nothing at all. */}
          {data?.directory && market && market.reason !== 'disabled' && (
            <div className="bg-card border border-border rounded-lg p-3 mb-4">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground inline-flex items-center gap-1.5">
                <TrendingUp size={13} /> Market price ranges
              </p>
              {market.available ? (
                market.rows.length === 0 ? (
                  <p className="text-xs text-muted-foreground mt-2">
                    Not enough data yet — ranges appear once 3+ companies have shared invoices for this shop.
                  </p>
                ) : (
                  <div className="mt-2 flex flex-col gap-1.5">
                    {market.rows.map((r) => (
                      <p key={`${r.dim_type}:${r.dim_key}`} className="text-sm">
                        <span className="text-foreground">{marketLabel(r)}</span>
                        <span className="mx-2 tabular-nums font-medium text-foreground">
                          ${r.p25.toLocaleString()} – ${r.p75.toLocaleString()}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          typical · {r.invoices} invoices from {r.companies} companies · last {r.window_months} months · prices vary by volume and situation
                        </span>
                      </p>
                    ))}
                  </div>
                )
              ) : (
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <p className="text-xs text-muted-foreground max-w-md">
                    See what other fleets typically pay at this shop. Available to accounts
                    that share their own <em>anonymized</em> price data — your company name and
                    invoices are never shown to anyone.
                  </p>
                  {canManageAccount && (
                    <button type="button" onClick={enableSharing} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition">
                      Enable sharing
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {workOrders.length === 0 ? (
            <p className="text-sm text-muted-foreground">No work orders linked to this vendor yet.</p>
          ) : (
            <DataGrid
              tableId="vendor-history"
              columns={columns}
              data={workOrders as unknown as Record<string, unknown>[]}
              searchKey={['vehicle_name', 'invoice_number']}
              searchPlaceholder="Search this vendor's work orders…"
              onRowClick={(row) => navigate(`/work-orders/${(row as unknown as WorkOrder).id}`)}
            />
          )}
        </>
      )}

      {/* Directory link dialog: search ACTIVE global entries. */}
      <Dialog open={linkOpen} onOpenChange={(o) => { if (!o) setLinkOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Link to the global directory</DialogTitle>
            <DialogDescription>
              Connect this vendor to its shared identity. Linking shares
              nothing about your account — it only tells the platform these
              are the same shop.
            </DialogDescription>
          </DialogHeader>
          <input
            type="text"
            value={dirQuery}
            onChange={(e) => setDirQuery(e.target.value)}
            placeholder="Search the directory…"
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
          />
          <div className="max-h-56 overflow-y-auto flex flex-col gap-1">
            {(dirResults?.entries ?? []).map((e) => (
              <button key={e.id} type="button" onClick={() => linkTo(e.id)} disabled={dirBusy}
                className="text-left px-3 py-2 rounded-md border border-border hover:bg-accent transition">
                <p className="text-sm text-foreground">{e.name}</p>
                <p className="text-xs text-muted-foreground">{[e.address, e.phone].filter(Boolean).join(' · ') || '—'}</p>
              </button>
            ))}
            {(dirResults?.entries ?? []).length === 0 && (
              <p className="text-xs text-muted-foreground px-1 py-2">
                No matching directory entries — use “Suggest to directory” instead.
              </p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Rate dialog — anonymous, moderated.  Verified-usage enforced
          server-side (only shops your work orders actually used). */}
      <Dialog open={rateOpen} onOpenChange={(o) => { if (!o) setRateOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Rate {data?.directory?.name}</DialogTitle>
            <DialogDescription>
              Shared anonymously with other fleets after moderation — your
              company is never shown, and none of your invoice data travels
              with it.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-1" role="radiogroup" aria-label="Rating">
            {[1, 2, 3, 4, 5].map(n => (
              <button key={n} type="button" role="radio" aria-checked={rateStars === n}
                onClick={() => setRateStars(n)}
                className="p-1"
                aria-label={`${n} star${n > 1 ? 's' : ''}`}>
                <Star size={22}
                  className={n <= rateStars ? 'text-warn fill-warn' : 'text-muted-foreground'} />
              </button>
            ))}
          </div>
          <textarea
            rows={3}
            value={rateComment}
            onChange={(e) => setRateComment(e.target.value)}
            placeholder="Optional — what should other fleets know about this shop?"
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
          />
          <DialogFooter className="gap-2">
            <Button variant="ghost" onClick={() => setRateOpen(false)}>Cancel</Button>
            <Button onClick={submitReview} disabled={dirBusy}>
              {dirBusy ? 'Submitting…' : 'Submit review'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Merge dialog — THIS vendor folds into the chosen survivor. */}
      <Dialog open={mergeOpen} onOpenChange={(o) => { if (!o) setMergeOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Merge “{vendor?.name}” into another vendor</DialogTitle>
            <DialogDescription>
              All of this vendor's work orders move to the vendor you pick,
              this record is deleted, and future synced invoices under this
              name resolve to the survivor. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <Select value={mergeTarget} onValueChange={(v) => setMergeTarget(String(v))}
            items={others.map(v => ({ value: String(v.id), label: v.name }))}>
            <SelectTrigger className="w-full" aria-label="Merge target"><SelectValue /></SelectTrigger>
            <SelectContent>
              {others.map(v => (
                <SelectItem key={v.id} value={String(v.id)}>{v.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter className="gap-2">
            <Button variant="ghost" onClick={() => setMergeOpen(false)}>Cancel</Button>
            <Button onClick={doMerge} disabled={!mergeTarget || merging}>
              {merging ? 'Merging…' : 'Merge'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
