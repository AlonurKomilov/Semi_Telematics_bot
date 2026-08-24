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
import { Store, ArrowLeft, Merge, Globe, History as HistoryIcon, Link2Off, Pencil, Star, TrendingUp } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { ActivityTrailDialog } from '../../components/activity-trail/ActivityTrailDialog';
import DataGrid from '../../components/datagrid';
import { PageHeader, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../components/ui/select';
import { Button } from '../../components/ui/button';
import { InfoTip, Tip } from '../../components/tooltip';
import { DIRECTORY_DISCLOSURE } from './directoryCopy';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useTaskLabels } from '../service-tasks/useTaskLabels';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { toneClasses, type Tone } from '../../lib/status';
import type { Vendor, WorkOrder, AnyColumn, DirectoryEntry, MarketRollupRow } from '../../types';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';

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
  // Two dedup scopes, two verbs: 'mine' = destructive fold into another
  // of YOUR vendors; 'directory' = non-destructive LINK to the public
  // entry the auto name-match missed (reversible via Unlink).
  const [mergeScope, setMergeScope] = useState<'mine' | 'directory'>('mine');
  const [dirTerm, setDirTerm] = useState('');
  const [dirPick, setDirPick] = useState<DirectoryEntry | null>(null);

  // Edit dialog — contact/identity on the registry record (never
  // rewrites work-order snapshots; server enforces that).
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', phone: '', email: '', address: '', notes: '' });

  const { data, isLoading, error } = useQuery<{
    vendor: Vendor; work_orders: WorkOrder[]; directory: DirectoryEntry | null;
    directory_status: 'linked' | 'private' | 'pending' | 'collecting';
  }>({
    queryKey: ['vendor', vendorId],
    queryFn: () => apiJSON(`/vendors/${vendorId}`),
    enabled: Number.isFinite(vendorId),
  });
  // Directory pipeline is AUTOMATIC (service recorded -> identity
  // reviewed -> linked); the page only displays state + a corrective
  // Unlink.  dirBusy still serializes the remaining actions.
  const [dirBusy, setDirBusy] = useState(false);
  const dirAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    setDirBusy(true);
    try {
      await fn();
      toast.success(okMsg);
      qc.invalidateQueries({ queryKey: ['vendor', vendorId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    } finally { setDirBusy(false); }
  };
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
  const { data: market } = useQuery<{ available: boolean; reason: string; rows: MarketRollupRow[]; available_count?: number }>({
    queryKey: ['vendor-market', dirId],
    queryFn: () => apiJSON(`/vendors/directory/${dirId}/market`),
    enabled: !!dirId,
  });
  const enableSharing = () => dirAction(async () => {
    await apiJSON('/vendors/market-sharing', { method: 'PUT', body: { enabled: true } });
    qc.invalidateQueries({ queryKey: ['vendor-market'] });
  }, 'Market data sharing enabled');
  // Task labels come from the account's service_tasks list (SSOT),
  // not a hardcoded copy — the old TASK_TYPE_OPTIONS had drifted.
  const { labelOf: taskLabelOf } = useTaskLabels();
  const marketLabel = (r: MarketRollupRow): string => {
    if (r.dim_type === 'part') return r.dim_label || r.dim_key;
    return taskLabelOf(r.dim_key);
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

  const openEdit = () => {
    if (!vendor) return;
    setEditForm({
      name: vendor.name ?? '',
      phone: vendor.phone ?? '',
      email: vendor.email ?? '',
      address: vendor.address ?? '',
      notes: vendor.notes ?? '',
    });
    setEditOpen(true);
  };

  const saveEdit = async () => {
    if (!editForm.name.trim()) return;
    setEditBusy(true);
    try {
      await apiJSON(`/vendors/${vendorId}`, { method: 'PUT', body: editForm });
      toast.success('Vendor updated');
      qc.invalidateQueries({ queryKey: ['vendor', vendorId] });
      qc.invalidateQueries({ queryKey: ['vendors'] });
      setEditOpen(false);
    } catch (e) {
      // 409 = name collision with another vendor → the fix is a merge.
      toast.error(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setEditBusy(false);
    }
  };

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

  // Directory search for the dedup Link branch (identity fields only).
  const { data: dirSearch } = useQuery<{ entries: DirectoryEntry[] }>({
    queryKey: ['vendor-dir-search', dirTerm],
    queryFn: () => apiJSON(`/vendors/directory/search?q=${encodeURIComponent(dirTerm)}`),
    enabled: mergeOpen && mergeScope === 'directory' && dirTerm.trim().length >= 2,
    staleTime: 30_000,
  });

  const doLink = async () => {
    if (!dirPick) return;
    setMerging(true);
    try {
      // Non-destructive: the row + its work orders stay; identity
      // links and empty contact fields fill from the verified entry.
      await apiJSON(`/vendors/${vendorId}/link-directory/${dirPick.id}`, { method: 'POST' });
      toast.success('Linked to the directory entry — nothing was deleted. Unlink here reverses it.');
      qc.invalidateQueries({ queryKey: ['vendor', vendorId] });
      qc.invalidateQueries({ queryKey: ['vendors'] });
      setMergeOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Link failed');
    } finally {
      setMerging(false);
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
              <ArrowLeft className="size-3.5" />
              All vendors
            </button>
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded-md text-xs font-medium text-foreground transition"
            >
              <HistoryIcon className="size-3.5" />
              History
            </button>
            {canWrite && vendor && (
              <button
                type="button"
                onClick={openEdit}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded-md text-xs font-medium text-foreground transition min-h-tap"
              >
                <Pencil className="size-3.5" />
                Edit
              </button>
            )}
            {canWrite && others.length > 0 && (
              <Tip label="Fold this vendor into another (fixes typo-duplicates); its work orders move over.">
                <button
                  type="button"
                  onClick={() => {
                    setMergeScope('mine'); setMergeTarget('');
                    setDirTerm(''); setDirPick(null);
                    setMergeOpen(true);
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded-md text-xs font-medium text-foreground transition"
                >
                  <Merge className="size-3.5" />
                  Merge into…
                </button>
              </Tip>
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
            <Card padding="compact">
              <p className="text-xs text-muted-foreground">Contact</p>
              <p className="text-sm mt-1">{vendor.phone || '—'}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{vendor.email || ''}</p>
            </Card>
            <Card padding="compact">
              <p className="text-xs text-muted-foreground">Address</p>
              <p className="text-sm mt-1">{vendor.address || '—'}</p>
            </Card>
            <Card padding="compact">
              <p className="text-xs text-muted-foreground">Work Orders</p>
              <p className="text-xl font-bold tabular-nums">{totals.count}</p>
            </Card>
            <Card padding="compact">
              <p className="text-xs text-muted-foreground">Total Spent</p>
              <p className="text-xl font-bold tabular-nums">{money(totals.spent, 0)}</p>
              {totals.unpaid > 0 && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  {money(totals.unpaid, 0)} unpaid
                </p>
              )}
            </Card>
          </div>

          {/* Global directory status: the pipeline is fully automatic
              (contribute-on-address-complete, link-on-approve); only the
              shop's name/contact ever travel.  The sole manual control
              here is Unlink — the wrong-auto-match escape hatch. */}
          <Card padding="compact" className="mb-4 flex flex-wrap items-center gap-3">
            <Globe className="text-muted-foreground shrink-0 size-4" />
            {data?.directory ? (
              <>
                <div className="min-w-0">
                  <p className="text-sm text-foreground">
                    In global directory as <span className="font-medium">{data.directory.name}</span>
                    {(data.directory.rating_count ?? 0) > 0 && (
                      <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Star className="text-warn size-3" />
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
                    <Tip label="Anonymous rating — displayed with no company attribution, after platform moderation.">
                    <button type="button" onClick={() => {
                      setRateStars(data.directory?.my_review?.rating ?? 5);
                      setRateComment(data.directory?.my_review?.comment ?? '');
                      setRateOpen(true);
                    }} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-foreground transition min-h-tap">
                      <Star className="size-3.5" /> {data.directory.my_review ? 'Edit review' : 'Rate this shop'}
                    </button>
                    </Tip>
                    <button type="button" onClick={unlink} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted text-muted-foreground hover:text-foreground transition min-h-tap">
                      <Link2Off className="size-3.5" /> Unlink
                    </button>
                  </span>
                )}
              </>
            ) : data?.directory_status === 'private' ? (
              // Account turned directory contribution OFF in Settings —
              // the shop stays private; consuming the directory still works.
              <p className="text-sm text-muted-foreground">
                Directory contribution is turned off in Settings — this shop
                stays private to your account.
              </p>
            ) : data?.directory_status === 'pending' ? (
              // Identity already flowed to the platform review queue
              // automatically — nothing for the user to do.
              <p className="text-sm text-muted-foreground">
                Sent for directory review — this shop appears publicly (and on
                the map) once the platform verifies it. Only its name and
                contact info were shared, never your invoices.
              </p>
            ) : (
              // 'collecting' — the pipeline starts once an address exists.
              <p className="text-sm text-muted-foreground">
                Joins the global directory automatically once it has an
                address — add one via Edit, or it fills in from your next
                work order.
              </p>
            )}
          </Card>

          {/* Market ranges (Phase D) — renders only when the platform
              flag is live.  'not_sharing' shows the give-to-get pitch;
              'disabled' renders nothing at all. */}
          {data?.directory && market && market.reason !== 'disabled' && (
            <Card padding="compact" className="mb-4">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground inline-flex items-center gap-1.5">
                <TrendingUp className="size-3.5" /> Market price ranges
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
                    {(market.available_count ?? 0) > 0 ? (
                      <>
                        <span className="text-foreground font-medium">
                          {market.available_count} typical price range{(market.available_count ?? 0) > 1 ? 's' : ''} exist{(market.available_count ?? 0) === 1 ? 's' : ''} for this shop
                        </span>
                        {' '}— enable sharing to see them. Available to accounts that share
                        their own <em>anonymized</em> price data — your company name and
                        invoices are never shown to anyone.
                      </>
                    ) : (
                      <>
                        See what other fleets typically pay at this shop. Available to accounts
                        that share their own <em>anonymized</em> price data — your company name and
                        invoices are never shown to anyone.
                      </>
                    )}
                  </p>
                  {canManageAccount && (
                    <button type="button" onClick={enableSharing} disabled={dirBusy}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition min-h-tap">
                      Enable sharing
                    </button>
                  )}
                </div>
              )}
            </Card>
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

      {/* Edit dialog — registry contact/identity.  Historical invoices
          keep their snapshots; only the master record changes. */}
      <Dialog open={editOpen} onOpenChange={(o) => { if (!o) setEditOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Edit vendor</DialogTitle>
            <DialogDescription>
              Updates this registry record only — what past invoices said
              stays untouched.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2.5">
            {([
              ['name', 'Name', 'Shop name'],
              ['phone', 'Phone', '555-1234'],
              ['email', 'Email', 'shop@example.com'],
              ['address', 'Address', 'Street, City, State'],
            ] as const).map(([key, label, ph]) => (
              // col-reverse: the input stays FIRST in DOM order so the
              // implicit label keeps targeting it (an InfoTip <button>
              // before the input would steal the association — clicking
              // the label text would toggle the tip, not focus the field).
              <label key={key} className="flex flex-col-reverse gap-1">
                <input
                  type="text"
                  value={editForm[key]}
                  onChange={(e) => setEditForm((f) => ({ ...f, [key]: e.target.value }))}
                  placeholder={ph}
                  className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
                />
                <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
                  {label}
                  {key === 'address' && <InfoTip size={12} label={DIRECTORY_DISCLOSURE} />}
                </span>
              </label>
            ))}
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">Notes</span>
              <textarea
                rows={2}
                value={editForm.notes}
                onChange={(e) => setEditForm((f) => ({ ...f, notes: e.target.value }))}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
            </label>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="ghost" onClick={() => setEditOpen(false)}>Cancel</Button>
            <Button onClick={saveEdit} disabled={editBusy || !editForm.name.trim()}>
              {editBusy ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
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
                <Star
                  className={cn(n <= rateStars ? 'text-warn fill-warn' : 'text-muted-foreground', 'size-5')} />
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

      {/* Merge dialog — the same My-vs-Shared binary as the page tabs,
          so the same two words.  "My vendors" folds THIS vendor into
          the chosen survivor (destructive); "Shared" LINKS it to the
          public-directory entry the auto name-match missed
          (non-destructive, reversible via Unlink). */}
      <Dialog open={mergeOpen} onOpenChange={(o) => { if (!o) setMergeOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Resolve “{vendor?.name}” as a duplicate</DialogTitle>
            <DialogDescription>
              {mergeScope === 'mine'
                ? "All of this vendor's work orders move to the vendor you pick, this record is deleted, and future synced invoices under this name resolve to the survivor. This cannot be undone."
                : 'This vendor is the same real-world shop as a public directory entry. Linking keeps this record and its work orders — identity connects and empty contact fields fill from the verified entry. Reversible with Unlink.'}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-1 rounded-md border border-border p-1 bg-muted/40" role="tablist" aria-label="Duplicate scope">
            {([['mine', 'My vendors'], ['directory', 'Shared']] as const).map(([scope, label]) => (
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
            <Select value={mergeTarget} onValueChange={(v) => setMergeTarget(String(v))}
              items={others.map(v => ({ value: String(v.id), label: v.name }))}>
              <SelectTrigger className="w-full" aria-label="Merge target"><SelectValue /></SelectTrigger>
              <SelectContent>
                {others.map(v => (
                  <SelectItem key={v.id} value={String(v.id)}>
                    <span className="font-mono text-xs text-muted-foreground">#{v.id}</span> {v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="flex flex-col gap-2">
              <input
                className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
                placeholder="Search the public directory…"
                value={dirTerm}
                onChange={(e) => { setDirTerm(e.target.value); setDirPick(null); }}
              />
              {dirTerm.trim().length >= 2 && (
                <div className="max-h-48 overflow-y-auto rounded-md border border-border divide-y divide-border">
                  {(dirSearch?.entries ?? []).length === 0 ? (
                    <p className="p-2.5 text-sm text-muted-foreground">No matching directory entries.</p>
                  ) : (dirSearch?.entries ?? []).map((e) => (
                    <button
                      key={e.id}
                      type="button"
                      onClick={() => setDirPick(e)}
                      className={`w-full text-left p-2.5 text-sm transition ${
                        dirPick?.id === e.id ? 'bg-primary/10 text-foreground' : 'text-foreground hover:bg-muted'
                      }`}
                    >
                      <span className="font-medium">{e.name}</span>
                      {e.chain ? <span className="ml-2 text-2xs text-muted-foreground">{e.chain}</span> : null}
                      {e.address && <span className="block text-xs text-muted-foreground truncate">{e.address}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <DialogFooter className="gap-2">
            <Button variant="ghost" onClick={() => setMergeOpen(false)}>Cancel</Button>
            {mergeScope === 'mine' ? (
              <Button onClick={doMerge} disabled={!mergeTarget || merging}>
                {merging ? 'Merging…' : 'Merge'}
              </Button>
            ) : (
              <Button onClick={doLink} disabled={!dirPick || merging}>
                {merging ? 'Linking…' : 'Link'}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ActivityTrailDialog
        entityType="vendor"
        entityId={vendor?.id ?? null}
        title={`${vendor?.name ?? 'Vendor'} — activity history`}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />
    </div>
  );
}
