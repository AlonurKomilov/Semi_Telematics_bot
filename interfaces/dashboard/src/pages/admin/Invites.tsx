import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import type { InviteInfo, InvitesResponse } from '../../types';
import DataTable from '../../components/DataTable';
import type { AnyColumn } from '../../types';

const ROLE_BADGES: Record<string, string> = {
  admin: 'bg-red-500/15 text-red-700 dark:text-red-400',
  fleet: 'bg-green-500/15 text-green-700 dark:text-green-400',
  safety: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
  dispatcher: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
  driver: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-400',
};

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatcher',
  driver: 'Driver',
};

function StatusBadge({ invite }: { invite: InviteInfo }) {
  if (invite.is_used) return <span className="px-2 py-0.5 rounded-full text-xs bg-green-500/15 text-green-700 dark:text-green-400">Used</span>;
  if (invite.is_expired) return <span className="px-2 py-0.5 rounded-full text-xs bg-gray-500/20 text-muted-foreground">Expired</span>;
  return <span className="px-2 py-0.5 rounded-full text-xs bg-primary/15 text-primary">Pending</span>;
}

export default function Invites() {
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [botUsername, setBotUsername] = useState('4truckBot');

  // Create form
  const [showForm, setShowForm] = useState(false);
  const [role, setRole] = useState('fleet');
  const [department, setDepartment] = useState('general');
  const [truckNum, setTruckNum] = useState('');
  const [hours, setHours] = useState(24);
  const [creating, setCreating] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const d = await apiJSON<InvitesResponse>(`/admin/invites?pending_only=${!showAll}`);
      setInvites(d.invites || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [showAll]);

  useEffect(() => {
    apiJSON<{ bot_username: string }>('/auth/bot-info')
      .then(d => { if (d.bot_username) setBotUsername(d.bot_username); })
      .catch(() => {});
  }, []);

  async function create() {
    setCreating(true);
    try {
      const body: Record<string, unknown> = { role, department, hours };
      if (truckNum.trim()) body.truck_num = truckNum.trim();
      const inv = await apiJSON<InviteInfo>('/admin/invite', { method: 'POST', body });
      copyLink(inv.code);
      setShowForm(false);
      setTruckNum('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setCreating(false);
    }
  }

  function copyLink(code: string) {
    const url = `https://t.me/${botUsername}?start=join_${code}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(code);
      setTimeout(() => setCopied(null), 2000);
    });
  }

  const columns: AnyColumn[] = [
    {
      key: 'role',
      label: 'Role',
      render: (v) => {
        const cls = ROLE_BADGES[v as string] || 'bg-gray-500/20 text-muted-foreground';
        return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{ROLE_LABELS[v as string] || String(v)}</span>;
      },
    },
    { key: 'department', label: 'Department' },
    { key: 'truck_num', label: 'Truck', render: (v) => (v as string) || '—' },
    {
      key: 'expires_at',
      label: 'Expires',
      render: (v) => v ? new Date(v as string).toLocaleString() : '—',
    },
    {
      key: '_status',
      label: 'Status',
      render: (_, row) => <StatusBadge invite={row as unknown as InviteInfo} />,
    },
    {
      key: 'code',
      label: 'Link',
      render: (v) => {
        const code = v as string;
        return (
          <button
            onClick={() => copyLink(code)}
            className="text-primary hover:text-primary/80 text-xs"
            title="Copy invite link"
          >
            {copied === code ? '✅ Copied' : '📋 Copy'}
          </button>
        );
      },
    },
  ];

  if (error && invites.length === 0) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">🔗 Invites</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
              className="rounded bg-muted border-border"
            />
            Show all
          </label>
          <button onClick={() => setShowForm(true)} className="px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium transition-colors">
            + New Invite
          </button>
        </div>
      </div>

      {error && <p className="text-destructive text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-muted-foreground text-sm py-8 text-center">Loading invites…</p>
      ) : invites.length === 0 ? (
        <p className="text-muted-foreground text-sm py-8 text-center">No invites found</p>
      ) : (
        <DataTable columns={columns} data={invites as unknown as Record<string, unknown>[]} />
      )}

      {/* Create modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowForm(false)}>
          <div className="bg-card rounded-xl border border-border p-6 w-96" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-4">Create Invite</h2>

            <label className="block text-sm text-muted-foreground mb-1">Role</label>
            <select value={role} onChange={(e) => setRole(e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3">
              {Object.entries(ROLE_LABELS).map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
            </select>

            <label className="block text-sm text-muted-foreground mb-1">Department</label>
            <input
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3"
            />

            {role === 'driver' && (
              <>
                <label className="block text-sm text-muted-foreground mb-1">Truck # (optional)</label>
                <input
                  value={truckNum}
                  onChange={(e) => setTruckNum(e.target.value)}
                  className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3"
                  placeholder="e.g. 101"
                />
              </>
            )}

            <label className="block text-sm text-muted-foreground mb-1">Expires in (hours)</label>
            <select value={hours} onChange={(e) => setHours(+e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-4">
              <option value={1}>1 hour</option>
              <option value={6}>6 hours</option>
              <option value={24}>24 hours</option>
              <option value={72}>3 days</option>
              <option value={168}>7 days</option>
              <option value={720}>30 days</option>
            </select>

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground">Cancel</button>
              <button onClick={create} disabled={creating} className="px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium disabled:opacity-50">
                {creating ? 'Creating...' : 'Create & Copy Link'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
