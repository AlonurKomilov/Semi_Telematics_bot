/**
 * Service Tasks — the shared task list behind Maintenance and Work
 * Orders (features/service_tasks).
 *
 * Standard tasks ship with every account and carry a cross-account
 * key, so "what does a brake job cost" compares honestly between
 * fleets; they're archive-only and name-locked for exactly that
 * reason.  Your own tasks are free to rename, archive or delete —
 * delete only while nothing references them, so history never loses
 * its label.
 */
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ClipboardList, Plus } from 'lucide-react';
import DataGrid, { type DataGridSegment } from '../../components/datagrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import type { MenuAction } from '../../components/ui/context-menu';
import { toneClasses } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import type { AnyColumn } from '../../types';
import {
  SERVICE_TASKS_KEY, createServiceTask, deleteServiceTask,
  fetchServiceTasks, updateServiceTask, type ServiceTask,
} from './api';

const SEGMENTS: DataGridSegment[] = [
  { key: 'all', label: 'All' },
  { key: 'standard', label: 'Standard', match: (r) => !!r.canonical_key },
  { key: 'mine', label: 'Your tasks', match: (r) => !r.canonical_key },
  { key: 'archived', label: 'Archived', match: (r) => r.status === 'archived' },
];

export default function ServiceTasks() {
  const qc = useQueryClient();
  const { has } = useViewPermissions();
  const canManage = has('can_service_tasks');

  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState('');
  const [hours, setHours] = useState('');
  const [saving, setSaving] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, 'all'],
    queryFn: () => fetchServiceTasks(true),
  });

  const rows = useMemo(
    () => (data?.service_tasks ?? []) as unknown as Record<string, unknown>[],
    [data],
  );

  const refresh = () => {
    qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
  };

  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      refresh();
      toast.success(label);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    }
  };

  const add = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await createServiceTask({
        name: name.trim(),
        expected_labor_hours: Number(hours) || 0,
      });
      setAddOpen(false);
      setName('');
      setHours('');
      refresh();
      toast.success('Service task added');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add that task');
    } finally {
      setSaving(false);
    }
  };

  const columns: AnyColumn[] = [
    { key: 'name', label: 'Task', sortable: true, filterable: true },
    {
      key: 'canonical_key', label: 'Source', sortable: true, filterable: true,
      render: (v) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(v ? 'info' : 'neutral')}`}>
          {v ? 'Standard' : 'Yours'}
        </span>
      ),
    },
    {
      key: 'expected_labor_hours', label: 'Est. labor', sortable: true,
      aggregable: true, aggFns: ['avg', 'max'],
      render: (v) => (Number(v)
        ? <span className="tabular-nums">{Number(v)} h</span>
        : <span className="text-muted-foreground">—</span>),
    },
    { key: 'description', label: 'Description', filterable: true,
      render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
    {
      key: 'status', label: 'Status', sortable: true, filterable: true,
      render: (v) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(v === 'archived' ? 'neutral' : 'ok')}`}>
          {v === 'archived' ? 'Archived' : 'Active'}
        </span>
      ),
    },
  ];

  const rowActions = canManage
    ? (row: Record<string, unknown>) => {
        const t = row as unknown as ServiceTask;
        const actions: MenuAction[] = [];
        if (t.status === 'active') {
          actions.push({
            key: 'archive', label: 'Archive',
            onSelect: () => act('Task archived', () =>
              updateServiceTask(t.id, { status: 'archived' })),
          });
        } else {
          actions.push({
            key: 'restore', label: 'Restore',
            onSelect: () => act('Task restored', () =>
              updateServiceTask(t.id, { status: 'active' })),
          });
        }
        if (!t.canonical_key) {
          actions.push({
            key: 'delete', label: 'Delete', danger: true, separatorBefore: true,
            onSelect: () => act('Task deleted', () => deleteServiceTask(t.id)),
          });
        }
        return actions;
      }
    : undefined;

  return (
    <div>
      <PageHeader
        icon={ClipboardList}
        title="Service Tasks"
        description="The shared list of work your fleet does — used by both maintenance schedules and work orders, so the same job is named the same way everywhere."
        actions={canManage ? (
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add service task
          </Button>
        ) : undefined}
      />

      {error && <ErrorState message={(error as Error).message} />}
      {isLoading && <TableSkeleton />}
      {!isLoading && !error && rows.length === 0 && (
        <EmptyState
          icon={ClipboardList}
          title="No service tasks yet"
          description="Standard tasks are added automatically — if this list is empty, try reloading."
        />
      )}
      {!isLoading && !error && rows.length > 0 && (
        <DataGrid
          data={rows}
          columns={columns}
          segments={SEGMENTS}
          rowActions={rowActions}
          searchKey={['name', 'description']}
          searchPlaceholder="Search service tasks…"
          tableId="service-tasks"
        />
      )}

      <Dialog open={addOpen} onOpenChange={(o) => { if (!o) setAddOpen(false); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add service task</DialogTitle>
            <DialogDescription>
              Names are unique — if the task already exists, use it instead of
              adding a second spelling (two spellings split every report).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">Name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Kingpin Service"
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Estimated labor hours (optional)
              </span>
              <input
                type="number" min="0" step="0.25" inputMode="decimal"
                value={hours}
                onChange={(e) => setHours(e.target.value)}
                placeholder="0"
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums text-foreground focus:outline-none focus:border-ring"
              />
            </label>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={add} disabled={saving || !name.trim()}>
              {saving ? 'Adding…' : 'Add task'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
