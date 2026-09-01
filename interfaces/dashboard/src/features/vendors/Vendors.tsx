/**
 * Vendors — the per-account repair-vendor registry, plus the public
 * directory browse surface.
 *
 * Two surface tabs (Team-Management pattern — separate datasets, NOT
 * segment tabs on one grid):
 *   • My vendors — one row per real-world shop this account uses, with
 *     usage rollups from linked work orders (Phase A master data).
 *   • Directory — platform-curated public shop identities (Phase C):
 *     identity fields + anonymous rating aggregate + whether one of MY
 *     vendors links to each entry.  Identity-only; no account's
 *     transactions ever appear here.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Globe, MapPin, Plus, Star, Store } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import { InfoTip } from '../../components/tooltip';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { DIRECTORY_DISCLOSURE } from './directoryCopy';
import type { Vendor, DirectoryEntry, AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { Badge } from '@/components/ui/badge';

function money(v: unknown): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const vendorColumns = (tz: string): AnyColumn[] => [
  { key: 'name', label: 'Vendor', sortable: true },
  {
    key: 'phone', label: 'Phone', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'address', label: 'Address', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'work_order_count', label: 'Work Orders', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
  },
  {
    key: 'total_spent', label: 'Total Spent', sortable: true,
    render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
  },
  {
    key: 'last_service_date', label: 'Last Visit', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => (v
      ? formatDay(String(v), { timeZone: tz })
      : <span className="text-muted-foreground">—</span>),
  },
];

const directoryColumns: AnyColumn[] = [
  {
    key: 'name', label: 'Shop', sortable: true,
    render: (v, row) => {
      const r = row as unknown as DirectoryEntry;
      return (
        <span className="inline-flex items-center gap-2">
          <span>{String(v)}</span>
          {r.chain && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full border border-border text-2xs text-muted-foreground">
              {r.chain}
            </span>
          )}
        </span>
      );
    },
  },
  {
    key: 'services', label: 'Services', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'address', label: 'Address', sortable: false,
    render: (v, row) => {
      const r = row as unknown as DirectoryEntry;
      return (
        <span className="inline-flex items-center gap-1.5">
          {r.lat != null && <MapPin className="text-muted-foreground shrink-0 size-3" aria-label="On the live map" />}
          {v ? String(v) : <span className="text-muted-foreground">—</span>}
        </span>
      );
    },
  },
  {
    key: 'phone', label: 'Phone', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'rating_avg', label: 'Rating', sortable: true,
    render: (v, row) => {
      const r = row as unknown as DirectoryEntry;
      return (r.rating_count ?? 0) > 0 ? (
        <span className="inline-flex items-center gap-1 text-sm">
          <Star className="text-warn size-3.5" />
          <span className="tabular-nums">{String(v)}</span>
          <span className="text-xs text-muted-foreground">({r.rating_count})</span>
        </span>
      ) : <span className="text-muted-foreground">—</span>;
    },
  },
  {
    key: 'linked_vendor_name', label: 'Your Vendor', sortable: true,
    render: (v) => (v ? (
      <Badge tone="ok">
        {String(v)}
      </Badge>
    ) : <span className="text-muted-foreground">—</span>),
  },
];

const EMPTY_FORM = { name: '', phone: '', email: '', address: '', notes: '' };

export default function Vendors() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tz = useTimezone();
  const { has } = useViewPermissions();
  const canWrite = has('can_manage_work_orders');
  const [tab, setTab] = useState<'mine' | 'directory'>('mine');

  // Manual create — vendors also appear automatically from work-order
  // saves and Datatruck sync, but a shop you PLAN to use deserves a
  // record before its first invoice.  Idempotent server-side (an
  // existing name returns that vendor).
  const [addOpen, setAddOpen] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addForm, setAddForm] = useState(EMPTY_FORM);

  const createVendor = async () => {
    if (!addForm.name.trim()) return;
    setAddBusy(true);
    try {
      const v = await apiJSON<Vendor>('/vendors', { method: 'POST', body: addForm });
      toast.success('Vendor created');
      qc.invalidateQueries({ queryKey: ['vendors'] });
      setAddOpen(false);
      setAddForm(EMPTY_FORM);
      navigate(`/vendors/${v.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setAddBusy(false);
    }
  };

  const { data, isLoading, error } = useQuery<{ vendors: Vendor[] }>({
    queryKey: ['vendors'],
    queryFn: () => apiJSON<{ vendors: Vendor[] }>('/vendors'),
  });
  const vendors = data?.vendors ?? [];
  const fetchError = error instanceof Error ? error.message : '';

  const { data: dirData, isLoading: dirLoading, error: dirError } = useQuery<{ entries: DirectoryEntry[] }>({
    queryKey: ['directory-browse'],
    queryFn: () => apiJSON<{ entries: DirectoryEntry[] }>('/vendors/directory/browse'),
    enabled: tab === 'directory',
  });
  const entries = dirData?.entries ?? [];
  const dirFetchError = dirError instanceof Error ? dirError.message : '';

  return (
    <div>
      <PageHeader
        icon={Store}
        title="Vendors"
        description="Every repair shop your fleet uses — one record per vendor, with spend history rolled up from work orders."
        actions={canWrite ? (
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground hover:bg-primary-hover rounded-md text-xs font-medium transition min-h-tap"
          >
            <Plus className="size-3.5" />
            Add vendor
          </button>
        ) : undefined}
      />

      <div role="tablist" aria-label="Vendor sections" className="flex gap-1 mb-4 border-b border-border">
        {([
          { key: 'mine' as const, label: 'My vendors' },
          { key: 'directory' as const, label: 'Shared' },
        ]).map(({ key, label }) => {
          const sel = tab === key;
          return (
            <button
              key={key}
              role="tab"
              aria-selected={sel}
              onClick={() => setTab(key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                sel
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {tab === 'mine' ? (
        fetchError && vendors.length === 0 ? (
          <ErrorState message={fetchError} />
        ) : isLoading && vendors.length === 0 ? (
          <TableSkeleton rows={6} cols={6} />
        ) : vendors.length === 0 ? (
          <EmptyState
            icon={Store}
            title="No vendors yet"
            description="Vendors appear here automatically as you record work orders — every saved invoice links its shop to the registry."
          />
        ) : (
          <DataGrid
          // A failed REFETCH keeps these rows up, so the
          // ``length === 0`` guard above never fires — the operator
          // reads a stale table as current.  The band says so without
          // removing the rows or the controls.
          error={fetchError || undefined}
            tableId="vendors"
            columns={vendorColumns(tz)}
            data={vendors as unknown as Record<string, unknown>[]}
            searchKey={['name', 'phone', 'address', 'email']}
            searchPlaceholder="Search vendors…"
            onRowClick={(row) => navigate(`/vendors/${(row as unknown as Vendor).id}`)}
          />
        )
      ) : (
        dirFetchError && entries.length === 0 ? (
          <ErrorState message={dirFetchError} />
        ) : dirLoading && entries.length === 0 ? (
          <TableSkeleton rows={6} cols={6} />
        ) : entries.length === 0 ? (
          <EmptyState
            icon={Globe}
            title="The public directory is just getting started"
            description="Platform-verified repair shops appear here as they're approved. Your vendors join automatically once they have an address — only a shop's name and contact travel, never your invoices."
          />
        ) : (
          <DataGrid
            tableId="vendor-directory-browse"
            columns={directoryColumns}
            data={entries as unknown as Record<string, unknown>[]}
            searchKey={['name', 'address', 'services', 'phone', 'chain']}
            searchPlaceholder="Search the directory…"
            onRowClick={(row) => {
              const e = row as unknown as DirectoryEntry;
              if (e.linked_vendor_id) navigate(`/vendors/${e.linked_vendor_id}`);
            }}
          />
        )
      )}

      {/* Add-vendor dialog — same field set as the profile Edit dialog. */}
      <Dialog open={addOpen} onOpenChange={(o) => { if (!o) setAddOpen(false); }}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Add vendor</DialogTitle>
            <DialogDescription>
              Create a shop record before its first invoice — work orders and
              synced invoices under this name will link to it automatically.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2.5">
            {([
              ['name', 'Name (required)', 'Shop name'],
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
                  value={addForm[key]}
                  onChange={(e) => setAddForm((f) => ({ ...f, [key]: e.target.value }))}
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
                value={addForm.notes}
                onChange={(e) => setAddForm((f) => ({ ...f, notes: e.target.value }))}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
            </label>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button onClick={createVendor} disabled={addBusy || !addForm.name.trim()}>
              {addBusy ? 'Creating…' : 'Create vendor'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
