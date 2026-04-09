import { useState, useEffect, useCallback } from 'react';
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

export default function Tasks() {
  const [tasks, setTasks] = useState<MaintenanceTask[]>([]);
  const [loading, setLoading] = useState(true);
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

  // Edit form
  const [eStatus, setEStatus] = useState('');
  const [eDesc, setEDesc] = useState('');
  const [eDueDate, setEDueDate] = useState('');

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
      setShowAdd(false); setFVehicle(''); setFDesc(''); setFDueDate(''); setFDueMiles('');
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
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500">
            <option value="">All Statuses</option>
            {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
          </select>
          <button onClick={() => { setShowAdd(!showAdd); setError(''); }} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition">
            {showAdd ? 'Cancel' : '+ New Task'}
          </button>
        </div>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 grid grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Vehicle Name</label>
            <input required value={fVehicle} onChange={e => setFVehicle(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Type</label>
            <select value={fType} onChange={e => setFType(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500">
              <option value="inspection">Inspection</option>
              <option value="oil_change">Oil Change</option>
              <option value="tire_rotation">Tire Rotation</option>
              <option value="brake_service">Brake Service</option>
              <option value="engine_repair">Engine Repair</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Description</label>
            <input required value={fDesc} onChange={e => setFDesc(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Due Date</label>
            <input type="date" value={fDueDate} onChange={e => setFDueDate(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium transition">
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </form>
      )}

      {loading ? <p className="text-gray-500">Loading...</p> : (
        <DataTable columns={columns} data={tasks as unknown as Record<string, unknown>[]} searchKey="vehicle_name" onRowClick={(row) => {
          const t = row as unknown as MaintenanceTask;
          setSelected(t); setEStatus(t.status); setEDesc(t.description); setEDueDate(t.due_date || '');
        }} />
      )}

      {selected && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="w-96 bg-gray-900 border-l border-gray-800 p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{selected.vehicle_name}</h2>
              <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white">✕</button>
            </div>
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-gray-400">Type</dt><dd className="capitalize">{selected.task_type.replace(/_/g, ' ')}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-400">Created</dt><dd>{new Date(selected.created_at).toLocaleDateString()}</dd></div>
              {selected.due_miles && <div className="flex justify-between"><dt className="text-gray-400">Due Miles</dt><dd>{selected.due_miles.toLocaleString()}</dd></div>}
              {selected.recur_interval_days && <div className="flex justify-between"><dt className="text-gray-400">Recurrence</dt><dd>Every {selected.recur_interval_days} days</dd></div>}
            </dl>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Status</label>
                <select value={eStatus} onChange={e => setEStatus(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500">
                  {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Description</label>
                <textarea value={eDesc} onChange={e => setEDesc(e.target.value)} rows={3} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Due Date</label>
                <input type="date" value={eDueDate} onChange={e => setEDueDate(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <button onClick={handleUpdate} disabled={saving} className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded text-sm font-medium transition">
                {saving ? 'Saving...' : 'Update Task'}
              </button>
              <button onClick={() => handleDelete(selected.id)} className="w-full py-2 bg-red-600/80 hover:bg-red-600 rounded text-sm font-medium transition">
                Delete Task
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
