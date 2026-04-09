import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import type { SettingsResponse, WorkSchedule } from '../../types';

const ROLES = ['owner', 'admin', 'fleet_manager', 'dispatcher', 'driver'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function Settings() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // Track editable settings
  const [edits, setEdits] = useState<Record<string, string>>({});

  // Schedule form
  const [showSchedule, setShowSchedule] = useState(false);
  const [sLabel, setSLabel] = useState('');
  const [sStart, setSStart] = useState(8);
  const [sEnd, setSEnd] = useState(17);
  const [sRole, setSRole] = useState('driver');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiJSON<SettingsResponse>('/admin/settings');
      setData(d);
      setEdits(d.settings || {});
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSaveSetting = async (key: string) => {
    setSaving(true); setError('');
    try {
      await apiJSON('/admin/settings', { method: 'PUT', body: { key, value: edits[key] } });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleAddSchedule = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true); setError('');
    try {
      await apiJSON('/admin/schedules', { method: 'POST', body: { label: sLabel, start_hour: sStart, end_hour: sEnd, target_role: sRole } });
      setShowSchedule(false); setSLabel('');
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleDeleteSchedule = async (id: number) => {
    try {
      await apiJSON('/admin/schedules/' + id, { method: 'DELETE' });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Settings</h1>
      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* Account Info */}
      {data?.account && (
        <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">Account</h2>
          <dl className="grid grid-cols-3 gap-4 text-sm">
            <div><dt className="text-gray-400">Name</dt><dd>{data.account.name || '—'}</dd></div>
            <div><dt className="text-gray-400">Tier</dt><dd className="capitalize">{data.account.tier || 'basic'}</dd></div>
            <div><dt className="text-gray-400">Status</dt><dd>{data.account.is_active ? <span className="text-green-400">Active</span> : <span className="text-red-400">Inactive</span>}</dd></div>
          </dl>
        </section>
      )}

      {/* Editable Settings */}
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Configuration</h2>
        {Object.keys(edits).length === 0 ? (
          <p className="text-gray-500 text-sm">No settings configured yet.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(edits).map(([key, val]) => (
              <div key={key} className="flex items-center gap-3">
                <span className="text-sm text-gray-400 w-48 flex-shrink-0 truncate">{key}</span>
                <input value={val} onChange={e => setEdits(prev => ({ ...prev, [key]: e.target.value }))}
                  className="flex-1 bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
                <button onClick={() => handleSaveSetting(key)} disabled={saving} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded text-xs font-medium transition">Save</button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* AI Usage */}
      {data?.ai_usage && Object.keys(data.ai_usage).length > 0 && (
        <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">AI Usage</h2>
          <dl className="grid grid-cols-3 gap-4 text-sm">
            {Object.entries(data.ai_usage).map(([k, v]) => (
              <div key={k}><dt className="text-gray-400 capitalize">{k.replace(/_/g, ' ')}</dt><dd>{String(v)}</dd></div>
            ))}
          </dl>
        </section>
      )}

      {/* Work Schedules */}
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Work Schedules</h2>
          <button onClick={() => setShowSchedule(!showSchedule)} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded text-xs font-medium transition">
            {showSchedule ? 'Cancel' : '+ Add Schedule'}
          </button>
        </div>

        {showSchedule && (
          <form onSubmit={handleAddSchedule} className="grid grid-cols-5 gap-3 mb-4">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Label</label>
              <input required value={sLabel} onChange={e => setSLabel(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Start Hour</label>
              <select value={sStart} onChange={e => setSStart(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500">
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">End Hour</label>
              <select value={sEnd} onChange={e => setSEnd(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500">
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Role</label>
              <select value={sRole} onChange={e => setSRole(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500">
                {ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <button type="submit" disabled={saving} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium transition">
                {saving ? 'Saving...' : 'Add'}
              </button>
            </div>
          </form>
        )}

        {data?.schedules && data.schedules.length > 0 ? (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-gray-400 border-b border-gray-800">
              <th className="py-2 pr-4">Label</th><th className="py-2 pr-4">Hours</th><th className="py-2 pr-4">Role</th><th className="py-2">Actions</th>
            </tr></thead>
            <tbody>
              {data.schedules.map((s: WorkSchedule) => (
                <tr key={s.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 pr-4">{s.label}</td>
                  <td className="py-2 pr-4">{String(s.start_hour).padStart(2, '0')}:00 – {String(s.end_hour).padStart(2, '0')}:00</td>
                  <td className="py-2 pr-4 capitalize">{s.target_role.replace(/_/g, ' ')}</td>
                  <td className="py-2">
                    <button onClick={() => handleDeleteSchedule(s.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-gray-500 text-sm">No schedules configured.</p>
        )}
      </section>
    </div>
  );
}
