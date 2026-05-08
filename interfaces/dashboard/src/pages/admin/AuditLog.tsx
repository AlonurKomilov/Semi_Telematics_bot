import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ClipboardList } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { AuditLogEntry, AnyColumn } from '../../types';

const columns: AnyColumn[] = [
  { key: 'created_at', label: 'Time', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleString() : '—' },
  { key: 'action', label: 'Action', sortable: true },
  { key: 'user_id', label: 'User ID', sortable: true },
  { key: 'target_type', label: 'Target', sortable: true },
  { key: 'target_id', label: 'Target ID' },
  { key: 'details', label: 'Details', render: (v) => {
    const s = String(v || '');
    return s.length > 80 ? <span title={s}>{s.slice(0, 80)}…</span> : s;
  }},
];

export default function AuditLog() {
  const [limit, setLimit] = useState(100);

  const { data, isLoading: loading, error: queryError, refetch } = useQuery({
    queryKey: ['admin-audit', limit],
    queryFn: () => apiJSON<{ entries: AuditLogEntry[] }>('/admin/audit-log?limit=' + limit),
    placeholderData: (prev) => prev,
  });
  const entries = data?.entries ?? [];
  const error = queryError instanceof Error ? queryError.message : '';

  return (
    <div>
      <PageHeader
        icon={ClipboardList}
        title="Audit Log"
        description="Every change to your account — who did what and when. Useful for compliance and tracing accidental edits."
        actions={
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="bg-background border border-border rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-ring"
          >
            <option value={50}>Last 50</option>
            <option value={100}>Last 100</option>
            <option value={250}>Last 250</option>
            <option value={500}>Last 500</option>
          </select>
        }
      />

      {error ? (
        <ErrorState
          title="Couldn't load audit log"
          message={error}
          onRetry={() => refetch()}
        />
      ) : loading && entries.length === 0 ? (
        <TableSkeleton rows={8} cols={6} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title="No audit entries yet"
          description="Activity from invites, role changes, and admin edits will appear here as it happens."
        />
      ) : (
        <DataTable columns={columns} data={entries as unknown as Record<string, unknown>[]} searchKey="action" />
      )}
    </div>
  );
}
