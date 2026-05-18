import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Plus } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { PageHeader, ErrorState } from '../../components/shell';
import type { WorkSchedule } from '../../types';

const ROLE_COLORS: Record<string, string> = {
  all: 'bg-primary/20 border-primary',
  owner: 'bg-purple-500/30 border-purple-500',
  admin: 'bg-red-500/30 border-red-500',
  fleet: 'bg-green-500/30 border-green-500',
  safety: 'bg-orange-500/30 border-orange-500',
  dispatcher: 'bg-yellow-500/30 border-yellow-500',
  driver: 'bg-cyan-500/30 border-cyan-500',
};

const ROLE_LABELS: Record<string, string> = {
  all: 'All Roles',
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatcher',
  driver: 'Driver',
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);

function fmtHour(h: number) {
  if (h === 0) return '12 AM';
  if (h === 12) return '12 PM';
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

interface WorkHoursResponse {
  schedules: WorkSchedule[];
  count: number;
}

export default function WorkHours() {
  const { t } = useTranslation();
  const [schedules, setSchedules] = useState<WorkSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');

  // Form state
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [label, setLabel] = useState('');
  const [startHour, setStartHour] = useState(6);
  const [endHour, setEndHour] = useState(18);
  const [targetRole, setTargetRole] = useState('all');
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const d = await apiJSON<WorkHoursResponse>('/admin/work-hours');
      setSchedules(d.schedules || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function openCreate() {
    setEditId(null);
    setLabel('');
    setStartHour(6);
    setEndHour(18);
    setTargetRole('all');
    setShowForm(true);
  }

  function openEdit(s: WorkSchedule) {
    setEditId(s.id);
    setLabel(s.label);
    setStartHour(s.start_hour);
    setEndHour(s.end_hour);
    setTargetRole(s.target_role);
    setShowForm(true);
  }

  async function save() {
    setSaving(true);
    try {
      if (editId) {
        await apiJSON(`/admin/work-hours/${editId}`, {
          method: 'PUT',
          body: { label, start_hour: startHour, end_hour: endHour, target_role: targetRole },
        });
      } else {
        await apiJSON('/admin/work-hours', {
          method: 'POST',
          body: { label, start_hour: startHour, end_hour: endHour, target_role: targetRole },
        });
      }
      setShowForm(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    if (!confirm('Delete this schedule?')) return;
    try {
      await apiJSON(`/admin/work-hours/${id}`, { method: 'DELETE' });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  const filtered = roleFilter === 'all'
    ? schedules
    : schedules.filter((s) => s.target_role === roleFilter || s.target_role === 'all');

  // Unique roles in the data
  const roles = ['all', ...new Set(schedules.map((s) => s.target_role).filter((r) => r !== 'all'))];

  if (error && schedules.length === 0) {
    return (
      <div>
        <PageHeader
          icon={Clock}
          title={t('pages.work_hours_title')}
          description={t('pages.work_hours_desc_short')}
        />
        <ErrorState message={error} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={Clock}
        title={t('pages.work_hours_title')}
        description={t('pages.work_hours_desc_long')}
        actions={
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
          >
            <Plus size={13} />
            New schedule
          </button>
        }
      />

      {/* Role filter */}
      <div className="flex gap-2 mb-4">
        {roles.map((r) => (
          <button
            key={r}
            onClick={() => setRoleFilter(r)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              roleFilter === r ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {ROLE_LABELS[r] || r}
          </button>
        ))}
      </div>

      {/* Timeline header */}
      <div className="bg-card rounded-xl border border-border overflow-hidden">
        <div className="flex border-b border-border">
          <div className="w-48 shrink-0 px-4 py-2 text-xs text-muted-foreground font-medium">Schedule</div>
          <div className="flex-1 flex">
            {HOURS.filter((_, i) => i % 3 === 0).map((h) => (
              <div key={h} className="flex-1 text-center text-xs text-muted-foreground py-2">{fmtHour(h)}</div>
            ))}
          </div>
          <div className="w-20 shrink-0" />
        </div>

        {loading && <div className="p-8 text-center text-muted-foreground">Loading...</div>}

        {!loading && filtered.length === 0 && (
          <div className="p-8 text-center text-muted-foreground">No working hours configured</div>
        )}

        {filtered.map((s) => {
          const colorCls = ROLE_COLORS[s.target_role] || ROLE_COLORS.all;
          const start = s.start_hour;
          const end = s.end_hour;
          const wraps = end <= start;
          const leftPct = (start / 24) * 100;
          const widthPct = wraps ? ((24 - start + end) / 24) * 100 : ((end - start) / 24) * 100;

          return (
            <div key={s.id} className="flex items-center border-b border-border/50 hover:bg-muted/30">
              <div className="w-48 shrink-0 px-4 py-3">
                <div className="text-sm font-medium text-foreground/90 truncate">{s.label}</div>
                <div className="text-xs text-muted-foreground">{ROLE_LABELS[s.target_role] || s.target_role}</div>
              </div>
              <div className="flex-1 relative h-10">
                {wraps ? (
                  <>
                    {/* Bar from start to midnight */}
                    <div
                      className={`absolute top-1 bottom-1 rounded border ${colorCls}`}
                      style={{ left: `${leftPct}%`, right: '0%' }}
                    />
                    {/* Bar from midnight to end */}
                    <div
                      className={`absolute top-1 bottom-1 rounded border ${colorCls}`}
                      style={{ left: '0%', width: `${(end / 24) * 100}%` }}
                    />
                  </>
                ) : (
                  <div
                    className={`absolute top-1 bottom-1 rounded border ${colorCls}`}
                    style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                  >
                    <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-foreground/80 truncate px-1">
                      {fmtHour(start)} – {fmtHour(end)}
                    </span>
                  </div>
                )}
              </div>
              <div className="w-20 shrink-0 flex gap-1 px-2">
                <button onClick={() => openEdit(s)} className="p-1 text-muted-foreground hover:text-primary text-sm" title="Edit">✏️</button>
                <button onClick={() => remove(s.id)} className="p-1 text-muted-foreground hover:text-destructive text-sm" title="Delete">🗑️</button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Create / Edit modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowForm(false)}>
          <div className="bg-card rounded-xl border border-border p-6 w-96" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-4">{editId ? t('modals.edit_schedule') : t('modals.new_schedule')}</h2>

            <label className="block text-sm text-muted-foreground mb-1">Label</label>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3"
              placeholder={t('forms.name_example')}
            />

            <div className="flex gap-3 mb-3">
              <div className="flex-1">
                <label className="block text-sm text-muted-foreground mb-1">Start</label>
                <select value={startHour} onChange={(e) => setStartHour(+e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border">
                  {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                </select>
              </div>
              <div className="flex-1">
                <label className="block text-sm text-muted-foreground mb-1">End</label>
                <select value={endHour} onChange={(e) => setEndHour(+e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border">
                  {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                </select>
              </div>
            </div>

            <label className="block text-sm text-muted-foreground mb-1">Target Role</label>
            <select value={targetRole} onChange={(e) => setTargetRole(e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-4">
              {Object.entries(ROLE_LABELS).map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
            </select>

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground">{t('common.cancel')}</button>
              <button onClick={save} disabled={saving || !label.trim()} className="px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium disabled:opacity-50">
                {saving ? 'Saving...' : editId ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
