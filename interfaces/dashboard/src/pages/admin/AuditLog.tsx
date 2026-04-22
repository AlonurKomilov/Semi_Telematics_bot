import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { AuditLogEntry, AnyColumn } from '../../types';

const columns: AnyColumn[] = [
  { key: 'created_at', label: 'Time', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleString() : '—' },
  { key: 'action', label: 'Action', sortable: true },
  { key: 'user_id', label: 'User ID', sortable: true },
  { key: 'target_type', label: 'Target', sortable: true },
  { key: 'target_id', label: 'Target ID' },
  { key: 'details', label: 'Details', render: (v) => {
    const s = String(v || '');
    return s.length > 80 ? <span title={s}>{s.slice(0, 80)}…</span> : s;
  }},
];

export default function AuditLog() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [limit, setLimit] = useState(100);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiJSON<{ entries: AuditLogEntry[] }>('/admin/audit-log?limit=' + limit);
      setEntries(data.entries || []);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, [limit]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Audit Log</h1>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))} className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500">
          <option value={50}>Last 50</option>
          <option value={100}>Last 100</option>
          <option value={250}>Last 250</option>
          <option value={500}>Last 500</option>
        </select>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading ? <p className="text-gray-500">Loading...</p> : (
        <DataTable columns={columns} data={entries as unknown as Record<string, unknown>[]} searchKey="action" />
      )}
    </div>
  );
}
