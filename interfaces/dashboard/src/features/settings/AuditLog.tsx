import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { ClipboardList } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { Tip } from '../../components/tooltip';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { AuditLogEntry, AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

const LIMIT_ITEMS = [
  { value: '50', label: 'Last 50' },
  { value: '100', label: 'Last 100' },
  { value: '250', label: 'Last 250' },
  { value: '500', label: 'Last 500' },
];

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
  // AI copilot actions — actor is the human who approved/undid, never
  // the model.  The "AI:" prefix clusters them in the column filter,
  // giving owners an "AI actions only" view for free.
  'ai_write:import_inventory_items': 'AI: imported inventory items',
  'ai_undo:import_inventory_items': 'AI: undid inventory import',
  'ai_write:create_maintenance_task': 'AI: created maintenance task',
  'ai_write:acknowledge_alerts': 'AI: acknowledged alerts',
};

/** Friendly label with a generic fallback for FUTURE AI tools — an
 *  unmapped ai_write:/ai_undo: still reads as an AI action, never a
 *  raw wire key. */
function actionLabel(s: string): string {
  if (ACTION_LABEL[s]) return ACTION_LABEL[s];
  if (s.startsWith('ai_write:')) return `AI action: ${s.slice(9).replace(/_/g, ' ')}`;
  if (s.startsWith('ai_undo:')) return `AI undo: ${s.slice(8).replace(/_/g, ' ')}`;
  return s;
}

const makeColumns = (tz: string): AnyColumn[] => [
  { key: 'created_at', label: 'Time', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => v ? formatDate(String(v), { timeZone: tz }) : '—' },
  {
    key: 'action',
    label: 'Action',
    sortable: true,
    // Action codes are a bounded enum — filter matches the raw code,
    // dropdown shows the same friendly label the cell renders.
    filterable: true,
    filterValue: (row) => String((row as { action?: string }).action ?? ''),
    filterLabel: (row) => actionLabel(String((row as { action?: string }).action ?? '')),
    render: (v) => actionLabel(String(v ?? '')),
  },
  { key: 'user_id', label: 'User ID', sortable: true, filterable: true },
  { key: 'target_type', label: 'Target', sortable: true, filterable: true },
  { key: 'target_id', label: 'Target ID' },
  { key: 'details', label: 'Details', render: (v) => {
    const s = String(v || '');
    return s.length > 80
      ? <Tip label={s}><span>{s.slice(0, 80)}…</span></Tip>
      : s;
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
          <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))} items={LIMIT_ITEMS}>
            <SelectTrigger aria-label="Number of entries to show"><SelectValue /></SelectTrigger>
            <SelectContent>
              {LIMIT_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
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
        <DataGrid tableId="audit-log" columns={columns} data={entries as unknown as Record<string, unknown>[]} searchKey={['action', 'details', 'target_id']} />
      )}
    </div>
  );
}
