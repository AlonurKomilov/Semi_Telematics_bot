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
import { useQuery } from '@tanstack/react-query';
import { Globe, MapPin, Star, Store } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import type { Vendor, DirectoryEntry, AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { toneClasses } from '../../lib/status';

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
  { key: 'name', label: 'Shop', sortable: true },
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
          {r.lat != null && <MapPin size={12} className="text-muted-foreground shrink-0" aria-label="On the live map" />}
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
          <Star size={14} className="text-warn" />
          <span className="tabular-nums">{String(v)}</span>
          <span className="text-xs text-muted-foreground">({r.rating_count})</span>
        </span>
      ) : <span className="text-muted-foreground">—</span>;
    },
  },
  {
    key: 'linked_vendor_name', label: 'Your Vendor', sortable: true,
    render: (v) => (v ? (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${toneClasses('ok')}`}>
        {String(v)}
      </span>
    ) : <span className="text-muted-foreground">—</span>),
  },
];

export default function Vendors() {
  const navigate = useNavigate();
  const tz = useTimezone();
  const [tab, setTab] = useState<'mine' | 'directory'>('mine');

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
      />

      <div role="tablist" aria-label="Vendor sections" className="flex gap-1 mb-4 border-b border-border">
        {([
          { key: 'mine' as const, label: 'My vendors' },
          { key: 'directory' as const, label: 'Directory' },
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
            description="Platform-verified repair shops appear here as they're approved. Suggest shops you trust from any vendor's page — only the shop's name and contact travel, never your invoices."
          />
        ) : (
          <DataGrid
            tableId="vendor-directory-browse"
            columns={directoryColumns}
            data={entries as unknown as Record<string, unknown>[]}
            searchKey={['name', 'address', 'services', 'phone']}
            searchPlaceholder="Search the directory…"
            onRowClick={(row) => {
              const e = row as unknown as DirectoryEntry;
              if (e.linked_vendor_id) navigate(`/vendors/${e.linked_vendor_id}`);
            }}
          />
        )
      )}
    </div>
  );
}
