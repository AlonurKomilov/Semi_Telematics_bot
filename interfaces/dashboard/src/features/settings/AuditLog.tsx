import { useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';

// Human-readable labels for the audit-log ``action`` enum.  Unknown
// actions fall through to the raw snake_case string so a newly-added
// action surfaces as e.g. ``invoice_void`` rather than ``[object
// Object]`` until someone adds the matching label here.  Extend this
// map whenever a new audit_log action ships on the backend.
const ACTION_LABEL: Record<string, string> = {
  invite_create: 'Invite created',
  invite_revoke: 'Invite revoked',
  invite_extend: 'Invite extended',
  invite_email_resent: 'Invite email resent',
  invite_declined: 'Invite declined by recipient',
  invite_email_bounced: 'Invite email bounced',
  invite_email_complained: 'Invite reported as spam',
};

const makeColumns = (tz: string): AnyColumn[] => [
  { key: 'created_at', label: 'Time', sortable: true, render: (v) => v ? formatDate(String(v), { timeZone: tz }) : '—' },
  {
    key: 'action',
    label: 'Action',
    sortable: true,
    render: (v) => {
      const s = String(v ?? '');
      return ACTION_LABEL[s] ?? s;
    },
  },
  { key: 'user_id', label: 'User ID', sortable: true },
  { key: 'target_type', label: 'Target', sortable: true },
  { key: 'target_id', label: 'Target ID' },
  { key: 'details', label: 'Details', render: (v) => {
    const s = String(v || '');
    return s.length > 80 ? <span title={s}>{s.slice(0, 80)}…</span> : s;
  }},
];

export default function AuditLog() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const [limit, setLimit] = useState(100);
  const columns = makeColumns(tz);

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
        title={t('pages.audit_log_title')}
        description={t('pages.audit_log_desc')}
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
