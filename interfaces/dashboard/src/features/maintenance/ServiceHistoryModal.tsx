import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Check, FileText, Receipt, X } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { apiJSON } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import type { MaintenanceTask } from '../../types';
import { TaskTypeCell } from './badges';
import { useTaskLabels } from '../service-tasks/useTaskLabels';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatDay } from '../../utils/datetime';

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

/** Slim invoice row merged into the vehicle timeline (Tier-2 B2).
 *  Present only for callers with can_work_orders_all. */
interface HistoryWorkOrder {
  id: number;
  service_date: string | null;
  vendor_name: string;
  invoice_number: string;
  repair_priority: string;
  payment_status: string;
  labor_cost: number;
  parts_cost: number;
  total_cost: number;
}

interface ServiceHistoryResponse {
  vehicle_name: string;
  tasks: MaintenanceTask[];
  work_orders?: HistoryWorkOrder[];
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
  const tz = useTimezone();
  // SSOT task labels (see useTaskLabels) — the cell no longer carries
  // a built-in map, so the caller supplies the display name.
  const { byValue: taskLabels } = useTaskLabels();
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

  // Tier-2 B2 — one chronological timeline: completed tasks + shop
  // invoices.  WOs already linked from a task are deduped (the task
  // card carries the link); the rest render as standalone invoice
  // cards.  Standalone-WO spend joins the Total Spend stat.
  const { entries, standaloneWoCents } = useMemo(() => {
    const tasks = (data?.tasks ?? []).map(t => ({
      kind: 'task' as const,
      date: t.completed_at || t.created_at || '',
      task: t,
      wo: undefined as HistoryWorkOrder | undefined,
    }));
    const linked = new Set(
      (data?.tasks ?? []).map(t => t.work_order_id).filter(Boolean),
    );
    const wos = (data?.work_orders ?? [])
      .filter(w => !linked.has(w.id))
      .map(w => ({
        kind: 'wo' as const,
        date: w.service_date || '',
        task: undefined as MaintenanceTask | undefined,
        wo: w,
      }));
    const merged = [...tasks, ...wos].sort((a, b) => (b.date > a.date ? 1 : -1));
    const cents = wos.reduce((s2, e) => s2 + Math.round((e.wo?.total_cost ?? 0) * 100), 0);
    return { entries: merged, standaloneWoCents: cents };
  }, [data]);
  const spendCents = totalCostCents + standaloneWoCents;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex justify-center items-start pt-12" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[80vh] bg-card border border-border rounded-xl p-6 overflow-y-auto shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">Service History</h2>
            <p className="text-sm text-muted-foreground">Vehicle #{vehicleName}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1">
            <X size={16} />
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
                  {spendCents > 0
                    ? `$${(spendCents / 100).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
                    : '—'}
                </p>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <p className="text-xs text-muted-foreground">Last Service</p>
                <p className="text-sm font-medium">
                  {data.summary.last_service_at
                    ? formatDay(data.summary.last_service_at, { timeZone: tz })
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
                      <TaskTypeCell type={type} customLabel={taskLabels[type]} />
                      <span className="text-muted-foreground tabular-nums">{count}</span>
                    </span>
                  ))}
              </div>
            )}

            {entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No completed services yet.</p>
            ) : (
              <ul className="space-y-2">
                {entries.map(entry => entry.kind === 'wo' && entry.wo ? (
                  <li
                    key={`wo-${entry.wo.id}`}
                    className="flex items-start gap-3 p-3 bg-muted/40 border border-border rounded-lg"
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="inline-flex items-center gap-1.5 text-foreground font-medium">
                          <Receipt size={14} className="text-muted-foreground" />
                          Work order{entry.wo.invoice_number ? ` · Inv ${entry.wo.invoice_number}` : ''}
                        </span>
                        <StatusBadge status={entry.wo.payment_status} />
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        {entry.date ? formatDate(entry.date, { timeZone: tz }) : '—'}
                        {entry.wo.vendor_name && <> · {entry.wo.vendor_name}</>}
                        {entry.wo.total_cost > 0 && (
                          <> · ${Number(entry.wo.total_cost).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                            {entry.wo.labor_cost > 0 || entry.wo.parts_cost > 0
                              ? ` (parts $${Number(entry.wo.parts_cost).toLocaleString(undefined, { maximumFractionDigits: 0 })} · labor $${Number(entry.wo.labor_cost).toLocaleString(undefined, { maximumFractionDigits: 0 })})`
                              : ''}
                          </>
                        )}
                      </p>
                      <a
                        href={`/work-orders/${entry.wo.id}`}
                        className="text-xs mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted hover:bg-muted/80 border border-border rounded text-foreground"
                        onClick={e => e.stopPropagation()}
                      >
                        <FileText size={12} />
                        Open work order #{entry.wo.id}
                      </a>
                    </div>
                  </li>
                ) : entry.task ? ((task => (
                  <li
                    key={task.id}
                    className="flex items-start gap-3 p-3 bg-muted/40 border border-border rounded-lg"
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2 text-sm">
                        <TaskTypeCell type={task.task_type} customLabel={taskLabels[task.task_type]} />
                        <StatusBadge status={task.status} />
                      </div>
                      {task.description && (
                        <p className="text-sm mt-1 text-foreground/80">{task.description}</p>
                      )}
                      <p className="text-xs text-muted-foreground mt-1">
                        {task.completed_at
                          ? formatDate(task.completed_at, { timeZone: tz })
                          : formatDate(task.created_at, { timeZone: tz })}
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
                          <Check size={12} aria-hidden />
                          Attested by{' '}
                          <span className="font-medium">
                            {task.attested_by_name || `user ${task.attested_by}`}
                          </span>
                          {' '}on {formatDay(task.attested_at, { timeZone: tz })}
                        </p>
                      )}
                      {task.work_order_id && (
                        <a
                          href={`/work-orders/${task.work_order_id}`}
                          className="text-xs mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted hover:bg-muted/80 border border-border rounded text-foreground"
                          onClick={e => e.stopPropagation()}
                        >
                          <FileText size={12} aria-hidden />
                          Work Order #{task.work_order_id}
                        </a>
                      )}
                    </div>
                  </li>
                ))(entry.task)) : null)}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
