import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import type { MaintenanceTask } from '../../types';
import { TaskTypeCell } from './badges';

interface ServiceHistoryResponse {
  vehicle_name: string;
  tasks: MaintenanceTask[];
  summary: {
    total_completed: number;
    total_cancelled: number;
    by_type: Record<string, number>;
    last_service_at: string | null;
    first_service_at: string | null;
  };
}

export function ServiceHistoryModal({
  vehicleName,
  onClose,
}: {
  vehicleName: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['maintenance-history', vehicleName],
    queryFn: () => apiJSON<ServiceHistoryResponse>(
      '/maintenance/history/' + encodeURIComponent(vehicleName),
    ),
  });

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex justify-center items-start pt-12" onClick={onClose}>
      <div
        className="w-[640px] max-h-[80vh] bg-card border border-border rounded-xl p-6 overflow-y-auto shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">Service History</h2>
            <p className="text-sm text-muted-foreground">Vehicle #{vehicleName}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1">
            <X size={18} />
          </button>
        </div>

        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load history'}
          </p>
        )}

        {data && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-5">
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Completed</p>
                <p className="text-xl font-bold tabular-nums">{data.summary.total_completed}</p>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Cancelled</p>
                <p className="text-xl font-bold tabular-nums">{data.summary.total_cancelled}</p>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Last Service</p>
                <p className="text-sm font-medium">
                  {data.summary.last_service_at
                    ? new Date(data.summary.last_service_at).toLocaleDateString()
                    : '—'}
                </p>
              </div>
            </div>

            {Object.keys(data.summary.by_type).length > 0 && (
              <div className="flex flex-wrap gap-2 mb-5">
                {Object.entries(data.summary.by_type)
                  .sort(([, a], [, b]) => b - a)
                  .map(([type, count]) => (
                    <span
                      key={type}
                      className="inline-flex items-center gap-1.5 bg-muted px-2.5 py-1 rounded text-xs"
                    >
                      <TaskTypeCell type={type} />
                      <span className="text-muted-foreground tabular-nums">{count}</span>
                    </span>
                  ))}
              </div>
            )}

            {data.tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground">No completed services yet.</p>
            ) : (
              <ul className="space-y-2">
                {data.tasks.map(task => (
                  <li
                    key={task.id}
                    className="flex items-start gap-3 p-3 bg-muted/40 border border-border rounded-lg"
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2 text-sm">
                        <TaskTypeCell type={task.task_type} />
                        <StatusBadge status={task.status} />
                      </div>
                      {task.description && (
                        <p className="text-sm mt-1 text-foreground/80">{task.description}</p>
                      )}
                      <p className="text-xs text-muted-foreground mt-1">
                        {task.completed_at
                          ? new Date(task.completed_at).toLocaleString()
                          : new Date(task.created_at).toLocaleString()}
                        {task.last_odometer != null && (
                          <> · {Number(task.last_odometer).toLocaleString()} mi</>
                        )}
                        {task.last_engine_hours != null && (
                          <> · {Number(task.last_engine_hours).toLocaleString()} hrs</>
                        )}
                      </p>
                      {task.attested_at && (
                        <p className="text-xs mt-1 inline-flex items-center gap-1 text-green-700 dark:text-green-400">
                          <span aria-hidden>✓</span>
                          Attested by{' '}
                          <span className="font-medium">
                            {task.attested_by_name || `user ${task.attested_by}`}
                          </span>
                          {' '}on {new Date(task.attested_at).toLocaleDateString()}
                        </p>
                      )}
                      {task.work_order_id && (
                        <a
                          href={`/work-orders/${task.work_order_id}`}
                          className="text-xs mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted hover:bg-muted/80 border border-border rounded text-foreground"
                          onClick={e => e.stopPropagation()}
                        >
                          <span aria-hidden>📄</span>
                          Work Order #{task.work_order_id}
                        </a>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
