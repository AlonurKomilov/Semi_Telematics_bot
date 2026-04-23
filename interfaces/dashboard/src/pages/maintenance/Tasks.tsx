import { useState, useEffect, useCallback, useRef } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import type { MaintenanceTask, AnyColumn } from '../../types';

const STATUS_OPTIONS = ['pending', 'in_progress', 'completed', 'cancelled'];

const columns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'task_type', label: 'Type', sortable: true, render: (v) => <span className="capitalize">{String(v || '').replace(/_/g, ' ')}</span> },
  { key: 'description', label: 'Description', render: (v) => {
    const s = String(v || '');
    return s.length > 60 ? <span title={s}>{s.slice(0, 60)}…</span> : s;
  }},
  { key: 'due_date', label: 'Due Date', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
  { key: 'due_miles', label: 'Due Miles', render: (v) => v ? Number(v).toLocaleString() : '—' },
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
                <span className={`ml-auto text-xs flex-shrink-0 ${v.fuel_percent < 20 ? 'text-red-400' : 'text-green-400'}`}>
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

// ── Main component ─────────────────────────────────────────────

export default function Tasks() {
  const [tasks, setTasks] = useState<MaintenanceTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<MaintenanceTask | null>(null);
  const [saving, setSaving] = useState(false);

  // Fleet vehicle list for the vehicle picker
  const [fleetVehicles, setFleetVehicles] = useState<FleetVehicle[]>([]);
  const [fleetLoading, setFleetLoading] = useState(false);

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

  // Load fleet vehicles whenever the add form is opened
  useEffect(() => {
    if (!showAdd) return;
    if (fleetVehicles.length > 0) return; // already loaded
    setFleetLoading(true);
    apiJSON<{ vehicles: FleetVehicle[] }>('/fleet/vehicles?page_size=200')
      .then((d) => setFleetVehicles(d.vehicles || []))
      .catch(() => setFleetVehicles([]))
      .finally(() => setFleetLoading(false));
  }, [showAdd]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const load = useCallback(async () => {
    setLoading(true);
    const qs = statusFilter ? '?status=' + statusFilter : '';
    try {
      const data = await apiJSON<{ tasks: MaintenanceTask[] }>('/maintenance/tasks' + qs);
      setTasks(data.tasks || []);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

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
      setShowAdd(false); setFVehicle(''); setFDesc(''); setFDueDate(''); setFDueMiles(''); setFOdometer(null); setFleetVehicles([]);
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
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Maintenance</h1>
        <div className="flex items-center gap-3">
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="bg-muted border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:border-ring">
            <option value="">All Statuses</option>
            {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
          </select>
          <button onClick={() => { setShowAdd(!showAdd); setError(''); if (showAdd) setFleetVehicles([]); }} className="px-4 py-2 bg-primary hover:bg-primary/90 rounded-lg text-sm font-medium transition">
            {showAdd ? 'Cancel' : '+ New Task'}
          </button>
        </div>
      </div>

      {error && <p className="text-destructive text-sm mb-3">{error}</p>}

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
                <span className="ml-1 text-green-400">current: {fOdometer.toLocaleString()} mi</span>
              )}
            </label>
            <input
              type="number" min="0" step="1"
              value={fDueMiles} onChange={e => setFDueMiles(e.target.value)}
              placeholder={fOdometer != null ? String(Math.round(fOdometer)) : 'miles'}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="w-full px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium text-foreground transition">
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </form>
      )}

      {loading ? <p className="text-muted-foreground">Loading...</p> : (
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
              <button onClick={() => setSelected(null)} className="text-muted-foreground hover:text-foreground">✕</button>
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
                <input type="number" min="0" step="1" value={eDueMiles} onChange={e => setEDueMiles(e.target.value)} placeholder="miles" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
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
