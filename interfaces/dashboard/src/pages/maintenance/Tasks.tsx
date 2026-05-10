import { useState, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Wrench, Plus, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { MaintenanceTask, AnyColumn } from '../../types';

const STATUS_OPTIONS = ['pending', 'in_progress', 'completed', 'cancelled'];

// Mileage-progress cell: renders "247,500 / 250,000 mi (98%)" with a
// thin gauge bar. Hidden when the task has no due_miles or hasn't been
// touched by the mileage-check scheduler yet.
function MileageProgress({ row }: { row: MaintenanceTask }) {
  if (row.due_miles == null) return <>—</>;
  if (row.last_odometer == null) {
    return (
      <span className="text-muted-foreground text-xs">
        Due {Number(row.due_miles).toLocaleString()} mi
      </span>
    );
  }
  const pct = Math.max(0, Math.min(120, Math.round((row.last_odometer / row.due_miles) * 100)));
  const overdue = pct >= 100;
  const remaining = Math.round(row.due_miles - row.last_odometer);
  return (
    <div className="flex flex-col gap-1 min-w-[140px]">
      <div className="text-xs">
        {Number(row.last_odometer).toLocaleString()} / {Number(row.due_miles).toLocaleString()} mi
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full ${overdue ? 'bg-red-500' : pct >= 90 ? 'bg-orange-500' : 'bg-blue-500'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className={`text-xs ${overdue ? 'text-red-500 font-medium' : 'text-muted-foreground'}`}>
        {overdue
          ? `${Math.abs(remaining).toLocaleString()} mi overdue`
          : `${remaining.toLocaleString()} mi to go`}
      </div>
    </div>
  );
}

const columns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'task_type', label: 'Type', sortable: true, render: (v) => <span className="capitalize">{String(v || '').replace(/_/g, ' ')}</span> },
  { key: 'description', label: 'Description', render: (v) => {
    const s = String(v || '');
    return s.length > 60 ? <span title={s}>{s.slice(0, 60)}…</span> : s;
  }},
  { key: 'due_date', label: 'Due Date', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
  // Combined "Mileage Progress" replaces the bare "Due Miles" cell so the
  // operator can see how close each truck is to the next service without
  // doing the arithmetic in their head.
  { key: 'due_miles', label: 'Mileage Progress', render: (_v, row) => <MileageProgress row={row as MaintenanceTask} /> },
  { key: 'status', label: 'Status', sortable: true, render: (v) => <StatusBadge status={String(v)} /> },
  { key: 'updated_at', label: 'Updated', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
];

// ─── Vehicle Picker ────────────────────────────────────────────

interface FleetVehicle {
  name: string;
  company: string;
  status: string;
  fuel_percent: number | null;
  speed_mph: number;
}

const STATUS_DOT: Record<string, string> = {
  moving: 'bg-green-500',
  idle: 'bg-yellow-400',
  stopped: 'bg-red-500',
};

function VehiclePicker({
  value,
  onChange,
  vehicles,
  loading: fleetLoading,
}: {
  value: string;
  onChange: (name: string, vehicle: FleetVehicle | null) => void;
  vehicles: FleetVehicle[];
  loading: boolean;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Sync when parent clears value
  useEffect(() => { if (!value) setQuery(''); }, [value]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = query.trim()
    ? vehicles.filter(
        (v) =>
          v.name.toLowerCase().includes(query.toLowerCase()) ||
          v.company.toLowerCase().includes(query.toLowerCase()),
      )
    : vehicles;

  const select = (v: FleetVehicle) => {
    setQuery(v.name);
    setOpen(false);
    onChange(v.name, v);
  };

  return (
    <div ref={ref} className="relative">
      <input
        required
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          onChange(e.target.value, null);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={fleetLoading ? 'Loading vehicles…' : 'Search truck or company…'}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-72 max-h-64 overflow-y-auto bg-card border border-border rounded-lg shadow-xl text-sm">
          {filtered.map((v) => (
            <li
              key={v.name}
              onMouseDown={() => select(v)}
              className="flex items-center gap-2 px-3 py-2 hover:bg-muted cursor-pointer"
            >
              {/* Status dot */}
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_DOT[v.status] || 'bg-gray-500'}`} />
              {/* Truck name */}
              <span className="font-mono font-semibold flex-shrink-0">{v.name}</span>
              {/* Company badge */}
              <span className="px-1.5 py-0.5 bg-muted rounded text-xs text-muted-foreground flex-shrink-0">{v.company}</span>
              {/* Status label */}
              <span className="text-xs text-muted-foreground capitalize flex-shrink-0">{v.status}</span>
              {/* Fuel */}
              {v.fuel_percent != null && (
                <span className={`ml-auto text-xs flex-shrink-0 ${v.fuel_percent < 20 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'}`}>
                  ⛽ {Math.round(v.fuel_percent)}%
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─── Miles Interval Picker ────────────────────────────────────

const MILE_PRESETS = [3_000, 5_000, 10_000, 15_000, 25_000, 50_000];

function MilesPicker({
  value,
  onChange,
  baseMiles,
  placeholder = 'miles',
}: {
  value: string;
  onChange: (v: string) => void;
  baseMiles?: number | null;
  placeholder?: string;
}) {
  return (
    <div>
      <input
        type="number"
        min="0"
        step="1"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      <div className="flex flex-wrap gap-1 mt-1.5">
        {MILE_PRESETS.map(p => {
          const base = baseMiles != null ? Math.round(baseMiles) : (value ? Number(value) : 0);
          const target = base + p;
          const label = p >= 1000 ? `+${p / 1000}k` : `+${p}`;
          const tip = baseMiles != null
            ? `${target.toLocaleString()} mi (odometer + ${p.toLocaleString()})`
            : `${p.toLocaleString()} mi`;
          return (
            <button
              key={p}
              type="button"
              title={tip}
              onClick={() => onChange(String(target))}
              className="px-1.5 py-0.5 text-xs bg-muted border border-border rounded hover:bg-primary/20 hover:border-primary/50 transition-colors cursor-pointer"
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────

export default function Tasks() {
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<MaintenanceTask | null>(null);
  const [saving, setSaving] = useState(false);

  // Add form
  const [fVehicle, setFVehicle] = useState('');
  const [fType, setFType] = useState('inspection');
  const [fDesc, setFDesc] = useState('');
  const [fDueDate, setFDueDate] = useState('');
  const [fDueMiles, setFDueMiles] = useState('');
  const [fOdometer, setFOdometer] = useState<number | null>(null);
  const [fOdometerLoading, setFOdometerLoading] = useState(false);

  // Edit form
  const [eStatus, setEStatus] = useState('');
  const [eDesc, setEDesc] = useState('');
  const [eDueDate, setEDueDate] = useState('');
  const [eDueMiles, setEDueMiles] = useState('');

  // Fleet vehicle list for the vehicle picker — only fetched once the
  // add form opens (the picker is the only consumer). Cached for 60s by
  // the global QueryClient default so reopening the form is instant.
  const { data: fleetData, isLoading: fleetLoading } = useQuery({
    queryKey: ['maintenance-fleet-vehicles'],
    queryFn: () => apiJSON<{ vehicles: FleetVehicle[] }>('/vehicles?page_size=200'),
    enabled: showAdd,
  });
  const fleetVehicles = fleetData?.vehicles ?? [];

  const fetchOdometer = async (name: string) => {
    if (!name.trim()) { setFOdometer(null); return; }
    setFOdometerLoading(true);
    try {
      const data = await apiJSON<{ odometer_miles: number | null }>(
        '/maintenance/odometer/' + encodeURIComponent(name.trim()),
      );
      setFOdometer(data.odometer_miles ?? null);
    } catch { setFOdometer(null); }
    finally { setFOdometerLoading(false); }
  };

  const tasksKey = ['maintenance-tasks', statusFilter] as const;
  const { data: tasksData, isLoading: loading, error: queryError } = useQuery({
    queryKey: tasksKey,
    queryFn: () => {
      const qs = statusFilter ? '?status=' + statusFilter : '';
      return apiJSON<{ tasks: MaintenanceTask[] }>('/maintenance/tasks' + qs);
    },
    placeholderData: (prev) => prev,
  });
  const tasks = tasksData?.tasks ?? [];
  const fetchError = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['maintenance-tasks'] });

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true); setError('');
    try {
      await apiJSON('/maintenance/tasks', { method: 'POST', body: {
        vehicle_name: fVehicle,
        task_type: fType,
        description: fDesc,
        due_date: fDueDate || undefined,
        due_miles: fDueMiles ? Number(fDueMiles) : undefined,
      }});
      setShowAdd(false); setFVehicle(''); setFDesc(''); setFDueDate(''); setFDueMiles(''); setFOdometer(null);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleUpdate = async () => {
    if (!selected) return;
    setSaving(true); setError('');
    const body: Record<string, unknown> = {};
    if (eStatus !== selected.status) body.status = eStatus;
    if (eDesc !== selected.description) body.description = eDesc;
    if (eDueDate !== (selected.due_date || '')) body.due_date = eDueDate || null;
    if (eDueMiles !== String(selected.due_miles || '')) body.due_miles = eDueMiles ? Number(eDueMiles) : null;
    if (Object.keys(body).length === 0) { setSaving(false); return; }
    try {
      await apiJSON('/maintenance/tasks/' + selected.id, { method: 'PUT', body });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleDelete = async (id: number) => {
    try {
      await apiJSON('/maintenance/tasks/' + id, { method: 'DELETE' });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  return (
    <div>
      <PageHeader
        icon={Wrench}
        title="Maintenance"
        description="Scheduled service tasks across all vehicles — inspections, oil changes, tire rotations and more. Click a row to update status."
        actions={
          <div className="flex items-center gap-2">
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="bg-background border border-border rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-ring">
              <option value="">All statuses</option>
              {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
            </select>
            <button onClick={() => { setShowAdd(!showAdd); setError(''); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition">
              <Plus size={13} />
              {showAdd ? 'Cancel' : 'New task'}
            </button>
          </div>
        }
      />

      {(error || fetchError) && (
        <div className="mb-3">
          <ErrorState message={error || fetchError} />
        </div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Vehicle</label>
            <VehiclePicker
              value={fVehicle}
              vehicles={fleetVehicles}
              loading={fleetLoading}
              onChange={(name, vehicle) => {
                setFVehicle(name);
                setFOdometer(null);
                if (vehicle) fetchOdometer(vehicle.name);
              }}
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Type</label>
            <select value={fType} onChange={e => setFType(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
              <option value="inspection">Inspection</option>
              <option value="oil_change">Oil Change</option>
              <option value="tire_rotation">Tire Rotation</option>
              <option value="brake_service">Brake Service</option>
              <option value="engine_repair">Engine Repair</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Description</label>
            <input required value={fDesc} onChange={e => setFDesc(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Due Date</label>
            <input type="date" value={fDueDate} onChange={e => setFDueDate(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              Due Miles
              {fOdometerLoading && <span className="ml-1 text-muted-foreground">(fetching…)</span>}
              {fOdometer != null && !fOdometerLoading && (
                <span className="ml-1 text-green-600 dark:text-green-400">current: {fOdometer.toLocaleString()} mi</span>
              )}
            </label>
            <MilesPicker
              value={fDueMiles}
              onChange={setFDueMiles}
              baseMiles={fOdometer}
              placeholder={fOdometer != null ? String(Math.round(fOdometer)) : 'miles'}
            />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="w-full px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium text-foreground transition">
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </form>
      )}

      {loading && tasks.length === 0 ? (
        <TableSkeleton rows={6} cols={7} />
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title={statusFilter ? `No ${statusFilter.replace(/_/g, ' ')} tasks` : 'No maintenance tasks yet'}
          description="Create your first task — set a due date, due miles, or both, and we'll alert you as it approaches."
          action={
            <button onClick={() => setShowAdd(true)} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition">
              <Plus size={13} />
              New task
            </button>
          }
        />
      ) : (
        <DataTable columns={columns} data={tasks as unknown as Record<string, unknown>[]} searchKey="vehicle_name" onRowClick={(row) => {
          const t = row as unknown as MaintenanceTask;
          setSelected(t); setEStatus(t.status); setEDesc(t.description); setEDueDate(t.due_date || ''); setEDueMiles(String(t.due_miles || ''));
        }} />
      )}

      {selected && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="w-96 bg-card border-l border-border p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{selected.vehicle_name}</h2>
              <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X size={16} /></button>
            </div>
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-muted-foreground">Type</dt><dd className="capitalize">{selected.task_type.replace(/_/g, ' ')}</dd></div>
              <div className="flex justify-between"><dt className="text-muted-foreground">Created</dt><dd>{new Date(selected.created_at).toLocaleDateString()}</dd></div>
              {selected.due_miles && <div className="flex justify-between"><dt className="text-muted-foreground">Due Miles</dt><dd>{selected.due_miles.toLocaleString()}</dd></div>}
              {selected.recur_interval_days && <div className="flex justify-between"><dt className="text-muted-foreground">Recurrence</dt><dd>Every {selected.recur_interval_days} days</dd></div>}
            </dl>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Status</label>
                <select value={eStatus} onChange={e => setEStatus(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                  {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Description</label>
                <textarea value={eDesc} onChange={e => setEDesc(e.target.value)} rows={3} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Due Date</label>
                <input type="date" value={eDueDate} onChange={e => setEDueDate(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Due Miles</label>
                <MilesPicker value={eDueMiles} onChange={setEDueMiles} />
              </div>
              <button onClick={handleUpdate} disabled={saving} className="w-full py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition">
                {saving ? 'Saving...' : 'Update Task'}
              </button>
              <button onClick={() => handleDelete(selected.id)} className="w-full py-2 bg-destructive/80 hover:bg-destructive rounded text-sm font-medium transition">
                Delete Task
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
