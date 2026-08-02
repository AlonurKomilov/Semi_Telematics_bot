/**
 * Audit Log — the unified who-did-what feed.
 *
 * Reads /admin/activity: the activity_events trail + the loads and
 * inventory rich trails + the frozen thin log's human rows, one wire
 * shape.  Field-level old→new renders inline; bulk actions arrive
 * collapsed as ONE group row (count is the REAL count — the store
 * keeps every member) and expand into a dialog on demand.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArchiveRestore, ClipboardList, Layers } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import {
  ChangeLines, HistoryList, actionLabel as trailActionLabel,
  entityLabel, type ActivityEvent,
} from '../../components/history/HistoryList';
import type { AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

const LIMIT_ITEMS = [
  { value: '50', label: 'Last 50' },
  { value: '100', label: 'Last 100' },
  { value: '200', label: 'Last 200' },
];

// Legacy-arm actions that deserve a friendlier sentence than the
// generic snake→spaces fallback.  The trail's own action vocabulary
// (create/update/delete/…) is labeled by the shared history module.
const LEGACY_ACTION_LABEL: Record<string, string> = {
  invite_create: 'Invite created',
  invite_revoke: 'Invite revoked',
  invite_extend: 'Invite extended',
  invite_email_resent: 'Invite email resent',
  invite_declined: 'Invite declined by recipient',
  invite_email_bounced: 'Invite email bounced',
  invite_email_complained: 'Invite reported as spam',
  permissions_update: 'Permissions changed',
  manager_tier_change: 'Manager tier changed',
  'ai_write:import_inventory_items': 'AI: imported inventory items',
  'ai_undo:import_inventory_items': 'AI: undid inventory import',
  'ai_write:create_maintenance_task': 'AI: created maintenance task',
  'ai_write:acknowledge_alerts': 'AI: acknowledged alerts',
};

function actionLabel(s: string): string {
  if (LEGACY_ACTION_LABEL[s]) return LEGACY_ACTION_LABEL[s];
  if (s.startsWith('ai_write:')) return `AI action: ${s.slice(9).replace(/_/g, ' ')}`;
  if (s.startsWith('ai_undo:')) return `AI undo: ${s.slice(8).replace(/_/g, ' ')}`;
  return trailActionLabel(s);
}

const makeColumns = (
  tz: string,
  onExpandGroup: (e: ActivityEvent) => void,
): AnyColumn[] => [
  { key: 'created_at', label: 'Time', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => v ? formatDate(String(v), { timeZone: tz }) : '—' },
  { key: 'actor_name', label: 'User', sortable: true, filterable: true,
    render: (v, row) => String(v || ((row as ActivityEvent).actor_user_id == null ? 'System' : `#${(row as ActivityEvent).actor_user_id}`)) },
  {
    key: 'action',
    label: 'Action',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as ActivityEvent).action ?? ''),
    filterLabel: (row) => actionLabel(String((row as ActivityEvent).action ?? '')),
    render: (v, row) => {
      const e = row as unknown as ActivityEvent;
      return e.is_group
        ? `${actionLabel(String(v ?? ''))} · ${e.count} items`
        : actionLabel(String(v ?? ''));
    },
  },
  { key: 'entity_type', label: 'Feature', sortable: true, filterable: true,
    filterLabel: (row) => entityLabel(String((row as ActivityEvent).entity_type ?? '')),
    render: (v) => entityLabel(String(v ?? '')) },
  { key: 'entity_id', label: 'Target',
    render: (v, row) => {
      const e = row as unknown as ActivityEvent;
      if (!e.is_group) return String(v ?? '');
      const sample = (e.sample_entity_ids ?? []).join(', ');
      return e.count && e.count > (e.sample_entity_ids?.length ?? 0)
        ? `${sample}, …` : sample;
    } },
  { key: 'changes', label: 'Changes',
    render: (_v, row) => {
      const e = row as unknown as ActivityEvent;
      if (e.is_group) {
        return (
          <Button
            size="sm" variant="outline"
            onClick={(ev) => { ev.stopPropagation(); onExpandGroup(e); }}
          >
            <Layers size={14} /> View all {e.count}
          </Button>
        );
      }
      const n = Object.keys(e.changes ?? {}).length;
      if (n === 0) return e.note ? <span className="text-xs text-muted-foreground">{e.note}</span> : '—';
      return <ChangeLines changes={e.changes} max={3} />;
    } },
];

export default function AuditLog() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const [limit, setLimit] = useState(100);
  const [group, setGroup] = useState<ActivityEvent | null>(null);

  const { data, isLoading: loading, error: queryError, refetch } = useQuery({
    queryKey: ['admin-activity', limit],
    queryFn: () => apiJSON<{ events: ActivityEvent[] }>('/admin/activity?limit=' + limit),
    placeholderData: (prev) => prev,
  });
  const events = data?.events ?? [];
  const error = queryError instanceof Error ? queryError.message : '';

  const qc = useQueryClient();
  // Retroactive undo: the same group restore the toast calls, with no
  // time limit — the trail doesn't expire.
  const restoreGroup = useMutation({
    mutationFn: (gid: string) => apiJSON<{
      restored: number; conflicts: { entity_id: string }[];
    }>(`/activity/restore-group/${gid}`, { method: 'POST' }),
    onSuccess: (res) => {
      const n = res.conflicts?.length ?? 0;
      toast.success(
        n > 0
          ? `${res.restored} restored · ${n} already exist`
          : `${res.restored} record${res.restored === 1 ? '' : 's'} restored`,
      );
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(
      e instanceof Error ? e.message : 'Could not restore'),
  });

  const groupQuery = useQuery({
    queryKey: ['admin-activity-group', group?.group_id],
    queryFn: () => apiJSON<{ events: ActivityEvent[] }>(
      `/admin/activity/group/${group!.group_id}`),
    enabled: !!group?.group_id,
  });

  const columns = makeColumns(tz, setGroup);

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
          title="Couldn't load the activity feed"
          message={error}
          onRetry={() => refetch()}
        />
      ) : loading && events.length === 0 ? (
        <TableSkeleton rows={8} cols={6} />
      ) : events.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title="No activity yet"
          description="Team actions — edits, deletions, invites, permission changes — appear here with their before and after values."
        />
      ) : (
        <DataGrid
          tableId="audit-log"
          columns={columns}
          data={events as unknown as Record<string, unknown>[]}
          searchKey={['action', 'entity_type', 'entity_id', 'actor_name', 'note']}
        />
      )}

      {/* Bulk-action expansion: every member event, full values each. */}
      <Dialog open={!!group} onOpenChange={(open) => { if (!open) setGroup(null); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {group ? `${actionLabel(group.action)} · ${group.count} ${entityLabel(group.entity_type).toLowerCase()}s` : ''}
            </DialogTitle>
          </DialogHeader>
          {group?.group_id
            && (groupQuery.data?.events ?? []).some((e) => e.restorable) && (
            <div className="pb-2 border-b border-border">
              <Button
                size="sm" variant="outline"
                disabled={restoreGroup.isPending}
                onClick={() => restoreGroup.mutate(group.group_id!)}
              >
                <ArchiveRestore size={14} />
                Restore all {group.count}
              </Button>
              <p className="mt-1 text-2xs text-muted-foreground">
                Brings every record back with its original values. This
                deletion stays in the history either way.
              </p>
            </div>
          )}
          <div className="max-h-[60vh] overflow-y-auto pr-1">
            {groupQuery.isLoading ? (
              <p className="text-sm text-muted-foreground py-4 text-center">Loading…</p>
            ) : (
              <HistoryList
                events={groupQuery.data?.events ?? []}
                tz={tz}
                onRestore={(ev) => {
                  void apiJSON(
                    `/activity/restore/${ev.event_id ?? String(ev.id).replace('trail:', '')}`,
                    { method: 'POST' })
                    .then(() => {
                      toast.success('Record restored');
                      void groupQuery.refetch();
                      void qc.invalidateQueries();
                    })
                    .catch((e) => toast.error(
                      e instanceof Error ? e.message : 'Restore failed'));
                }}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
