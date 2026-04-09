import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import { useAuth } from '../../context/AuthContext';
import type { AdminUser, InviteInfo, AnyColumn } from '../../types';

const ROLES = ['admin', 'fleet_manager', 'dispatcher', 'driver'] as const;

const userColumns: AnyColumn[] = [
  { key: 'display_name', label: 'Name', sortable: true },
  { key: 'role', label: 'Role', sortable: true, render: (v) => <span className="capitalize">{String(v).replace('_', ' ')}</span> },
  { key: 'department', label: 'Department', sortable: true },
  { key: 'truck_num', label: 'Truck', sortable: true },
  { key: 'email', label: 'Email' },
  { key: 'is_active', label: 'Status', render: (v) => <StatusBadge status={v ? 'active' : 'inactive'} /> },
];

const inviteColumns: AnyColumn[] = [
  { key: 'code', label: 'Code', render: (v) => <code className="bg-gray-800 px-2 py-0.5 rounded text-xs">{String(v)}</code> },
  { key: 'role', label: 'Role', render: (v) => <span className="capitalize">{String(v).replace('_', ' ')}</span> },
  { key: 'department', label: 'Department' },
  { key: 'expires_at', label: 'Expires', render: (v) => v ? new Date(String(v)).toLocaleString() : '—' },
  { key: 'is_used', label: 'Status', render: (_, row) => {
    const r = row as unknown as InviteInfo;
    if (r.is_used) return <span className="text-gray-500">Used</span>;
    if (r.is_expired) return <span className="text-red-400">Expired</span>;
    return <span className="text-green-400">Active</span>;
  }},
];

export default function Users() {
  const { user: me } = useAuth();
  const [tab, setTab] = useState<'users' | 'invites'>('users');
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<AdminUser | null>(null);

  // Invite form
  const [showInvite, setShowInvite] = useState(false);
  const [invRole, setInvRole] = useState('fleet_manager');
  const [invDept, setInvDept] = useState('general');
  const [invTruck, setInvTruck] = useState('');
  const [invHours, setInvHours] = useState(24);
  const [inviting, setInviting] = useState(false);
  const [newCode, setNewCode] = useState('');

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiJSON<{ users: AdminUser[] }>('/admin/users');
      setUsers(data.users || []);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, []);

  const loadInvites = useCallback(async () => {
    try {
      const data = await apiJSON<{ invites: InviteInfo[] }>('/admin/invites?pending_only=false');
      setInvites(data.invites || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadUsers(); loadInvites(); }, [loadUsers, loadInvites]);

  const handleRoleChange = async (userId: number, role: string) => {
    try {
      await apiJSON('/admin/users/' + userId + '/role', { method: 'PUT', body: { role } });
      await loadUsers();
      setSelected(null);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  const handleToggleActive = async (userId: number, active: boolean) => {
    try {
      await apiJSON('/admin/users/' + userId + '/status', { method: 'PUT', body: { is_active: active } });
      await loadUsers();
      setSelected(null);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setInviting(true); setNewCode(''); setError('');
    try {
      const res = await apiJSON<{ code: string }>('/admin/invite', {
        method: 'POST',
        body: { role: invRole, department: invDept, truck_num: invTruck || null, hours: invHours },
      });
      setNewCode(res.code);
      loadInvites();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setInviting(false); }
  };

  const activeCount = users.filter(u => u.is_active).length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Team Management</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">{activeCount} active / {users.length} total</span>
          <button onClick={() => { setShowInvite(!showInvite); setNewCode(''); }} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition">
            {showInvite ? 'Cancel' : '+ Invite'}
          </button>
        </div>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {showInvite && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6">
          <h3 className="font-semibold mb-3">Create Invite Link</h3>
          <form onSubmit={handleInvite} className="grid grid-cols-5 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Role</label>
              <select value={invRole} onChange={e => setInvRole(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm">
                {ROLES.map(r => <option key={r} value={r}>{r.replace('_', ' ')}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Department</label>
              <input value={invDept} onChange={e => setInvDept(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Truck (optional)</label>
              <input value={invTruck} onChange={e => setInvTruck(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Expiry (hours)</label>
              <input type="number" min={1} max={720} value={invHours} onChange={e => setInvHours(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
            </div>
            <div className="flex items-end">
              <button type="submit" disabled={inviting} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium transition">
                {inviting ? 'Creating...' : 'Generate'}
              </button>
            </div>
          </form>
          {newCode && (
            <div className="mt-3 p-3 bg-green-900/30 border border-green-800 rounded-lg">
              <p className="text-sm text-green-400 mb-1">Invite created! Share this link:</p>
              <code className="text-sm bg-gray-800 px-3 py-1 rounded block break-all">
                {window.location.origin}/dashboard/?invite={newCode}
              </code>
              <p className="text-xs text-gray-500 mt-1">Or share the code directly: <strong>{newCode}</strong></p>
            </div>
          )}
        </div>
      )}

      <div className="flex gap-1 mb-4">
        {(['users', 'invites'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} className={`px-4 py-2 rounded-lg text-sm font-medium transition capitalize ${tab === t ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-white'}`}>
            {t} ({t === 'users' ? users.length : invites.length})
          </button>
        ))}
      </div>

      {loading && users.length === 0 ? <p className="text-gray-500">Loading...</p> : tab === 'users' ? (
        <>
          <DataTable columns={userColumns} data={users as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => setSelected(row as unknown as AdminUser)} />
          {selected && (
            <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
              <div className="w-96 bg-gray-900 border-l border-gray-800 p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold">{selected.display_name}</h2>
                  <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white">✕</button>
                </div>
                <dl className="space-y-3 text-sm">
                  <Row label="Role" value={selected.role.replace('_', ' ')} />
                  <Row label="Department" value={selected.department} />
                  <Row label="Truck" value={selected.truck_num || '—'} />
                  <Row label="Email" value={selected.email || '—'} />
                  <Row label="Telegram ID" value={String(selected.telegram_id)} />
                  <Row label="Language" value={selected.language || '—'} />
                  <Row label="Status" value={selected.is_active ? 'Active' : 'Inactive'} />
                </dl>
                <div className="mt-6 space-y-3">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Change Role</label>
                    <select
                      value={selected.role}
                      onChange={e => handleRoleChange(selected.id, e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm"
                    >
                      {ROLES.map(r => <option key={r} value={r}>{r.replace('_', ' ')}</option>)}
                    </select>
                  </div>
                  <button
                    onClick={() => handleToggleActive(selected.id, !selected.is_active)}
                    className={`w-full py-2 rounded text-sm font-medium transition ${selected.is_active ? 'bg-red-600/80 hover:bg-red-600' : 'bg-green-600/80 hover:bg-green-600'}`}
                  >
                    {selected.is_active ? 'Deactivate User' : 'Activate User'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      ) : (
        <DataTable columns={inviteColumns} data={invites as unknown as Record<string, unknown>[]} searchKey="code" />
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-gray-400">{label}</dt>
      <dd className="capitalize">{value}</dd>
    </div>
  );
}
