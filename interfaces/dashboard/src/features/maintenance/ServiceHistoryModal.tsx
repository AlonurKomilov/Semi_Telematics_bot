import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { X } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { apiJSON } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import type { MaintenanceTask } from '../../types';
import { TaskTypeCell } from './badges';

// Build the last-12-months service-count series for the chart.
// Anchored on TODAY so the rightmost bar is always the current month,
// and we backfill empty months with zero so the gap pattern is honest
// (a sparse history shouldn't compress into a misleading dense bar).
function buildMonthlySeries(tasks: MaintenanceTask[]): Array<{ label: string; count: number }> {
  const buckets = new Map<string, number>();
  const now = new Date();
  const months: Array<{ key: string; label: string }> = [];
  for (let i = 11; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const label = d.toLocaleDateString(undefined, { month: 'short' });
    months.push({ key, label });
    buckets.set(key, 0);
  }
  for (const t of tasks) {
    const ts = t.completed_at || t.created_at;
    if (!ts) continue;
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) continue;
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    if (buckets.has(key)) buckets.set(key, (buckets.get(key) ?? 0) + 1);
  }
  return months.map(m => ({ label: m.label, count: buckets.get(m.key) ?? 0 }));
}

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

  // Last-12-months bar chart series.  Memoized off the loaded tasks so
  // expanding / collapsing the rows below doesn't recompute the chart.
  const monthly = useMemo(
    () => (data ? buildMonthlySeries(data.tasks) : []),
    [data],
  );
  const monthlyTotal = monthly.reduce((s, m) => s + m.count, 0);
  // Spend total across the loaded tasks.  Integer-cents sum to avoid
  // float drift; rendered as one display string.
  const totalCostCents = useMemo(() => {
    if (!data) return 0;
    return data.tasks.reduce(
      (s, t) => s + (typeof t.cost_cents === 'number' ? t.cost_cents : 0),
      0,
    );
  }, [data]);

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
            <div className="grid grid-cols-4 gap-3 mb-5">
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Completed</p>
                <p className="text-xl font-bold tabular-nums">{data.summary.total_completed}</p>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Cancelled</p>
                <p className="text-xl font-bold tabular-nums">{data.summary.total_cancelled}</p>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Total Spend</p>
                <p className="text-xl font-bold tabular-nums">
                  {totalCostCents > 0
                    ? `$${(totalCostCents / 100).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
                    : '—'}
                </p>
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

            {/* Service activity over the last 12 months — a flat run
                of empty bars signals under-servicing at a glance, which
                is exactly the DOT-audit talking point this view exists
                for. */}
            {monthlyTotal > 0 && (
              <div className="mb-5 bg-muted/30 border border-border rounded-lg p-3">
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-xs font-medium text-muted-foreground">
                    Services in the last 12 months
                  </p>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {monthlyTotal} total
                  </p>
                </div>
                <div style={{ width: '100%', height: 100 }}>
                  <ResponsiveContainer>
                    <BarChart data={monthly} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                      <XAxis
                        dataKey="label"
                        stroke="currentColor"
                        style={{ fontSize: '10px', opacity: 0.6 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis hide />
                      <Tooltip
                        cursor={{ fill: 'rgba(120,120,120,0.08)' }}
                        contentStyle={{
                          background: 'var(--card)',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          fontSize: 12,
                        }}
                        formatter={(v: unknown) => [String(v), 'services']}
                      />
                      <Bar dataKey="count" fill="var(--chart-1)" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

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
                        {typeof task.cost_cents === 'number' && task.cost_cents > 0 && (
                          <> · ${(task.cost_cents / 100).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}{task.vendor_name ? ` @ ${task.vendor_name}` : ''}</>
                        )}
                      </p>
                      {task.attested_at && (
                        <p className="text-xs mt-1 inline-flex items-center gap-1 text-ok">
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
