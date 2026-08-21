import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Plus, Pencil, Trash2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { PageHeader, ErrorState } from '../../components/shell';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { toneClasses } from '../../lib/status';
import type { WorkSchedule } from '../../types';

// Per-role tones used for the timeline bars.  ``all`` rides the
// design system's primary; the rest hand-pick from the chart-color
// palette so each role gets a distinct band without violating the
// "no raw Tailwind palette" rule.  bg-* tokens map to OKLCH values
// in src/index.css so dark mode flips correctly.
const ROLE_BAR_CLS: Record<string, string> = {
  all:        'bg-primary/25 border-primary',
  owner:      'bg-chart-2/30 border-chart-2',
  admin:      'bg-chart-1/30 border-chart-1',
  fleet:      'bg-chart-3/30 border-chart-3',
  safety:     'bg-chart-4/30 border-chart-4',
  dispatcher: 'bg-chart-5/30 border-chart-5',
  driver:     'bg-muted-foreground/30 border-muted-foreground',
};

const ROLE_LABELS: Record<string, string> = {
  all: 'All Roles',
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatcher',
  hr: 'HR',
  accounting: 'Accounting',
  recruiter: 'Recruiter',
  driver: 'Driver',
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);

function fmtHour(h: number) {
  if (h === 0) return '12 AM';
  if (h === 12) return '12 PM';
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

const HOUR_ITEMS = HOURS.map((h) => ({ value: String(h), label: fmtHour(h) }));
const ROLE_ITEMS = Object.entries(ROLE_LABELS).map(([val, lbl]) => ({ value: val, label: lbl }));

interface WorkHoursResponse {
  schedules: WorkSchedule[];
  count: number;
}

/**
 * Working Hours panel — schedule list + timeline visualization +
 * create/edit modal.  Exported as a named component so the
 * Team Management page can render it inside its tab bar (the
 * standalone /admin/work-hours route has been retired in favour of
 * keeping all team-shift config under one Team Management nav entry).
 *
 * The thin default export below wraps this in a PageHeader for any
 * deep-link / bookmark to ``/admin/work-hours`` that still resolves —
 * the router redirects new visits to the consolidated team page.
 */
export function WorkHoursPanel() {
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
  // Inline delete confirmation — replaces the previous window.confirm()
  // so the destructive flow matches the rest of the dashboard's
  // confirm patterns (Invites revoke, Team-Management role-change, etc.).
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

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
    try {
      await apiJSON(`/admin/work-hours/${id}`, { method: 'DELETE' });
      setConfirmDeleteId(null);
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
    return <ErrorState message={error} />;
  }

  return (
    <div>
      {/* New-schedule action sits at the top-right of the panel so the
          tab + this action stay visually close.  Inside the Team
          Management host this replaces the PageHeader's actions slot. */}
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-muted-foreground">
          {t('pages.work_hours_desc_short', { defaultValue: "Define active hours per role — alerts pause outside these windows so the team isn't paged off-shift." })}
        </p>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition min-h-tap"
        >
          <Plus size={14} aria-hidden="true" />
          {t('actions.new_schedule', { defaultValue: 'New schedule' })}
        </button>
      </div>

      {/* Role filter */}
      <div className="flex flex-wrap gap-2 mb-4">
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

        {loading && <div className="p-8 text-center text-muted-foreground">Loading…</div>}

        {!loading && filtered.length === 0 && (
          <div className="p-8 text-center text-muted-foreground">No working hours configured</div>
        )}

        {filtered.map((s) => {
          const colorCls = ROLE_BAR_CLS[s.target_role] || ROLE_BAR_CLS.all;
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
                <button
                  onClick={() => openEdit(s)}
                  className="p-1 text-muted-foreground hover:text-primary"
                  aria-label="Edit schedule"
                  title="Edit"
                >
                  <Pencil size={14} aria-hidden="true" />
                </button>
                <button
                  onClick={() => setConfirmDeleteId(s.id)}
                  className="p-1 text-muted-foreground hover:text-destructive"
                  aria-label="Delete schedule"
                  title="Delete"
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Create / Edit modal — base-ui Dialog matches the rest of the
          dashboard's primitives (Invites + Team Management already use
          this; consistent ARIA + focus trap + Escape close). */}
      <Dialog open={showForm} onOpenChange={(open) => { if (!open) setShowForm(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editId ? t('modals.edit_schedule', { defaultValue: 'Edit schedule' }) : t('modals.new_schedule', { defaultValue: 'New schedule' })}</DialogTitle>
            <DialogDescription>
              {t('modals.work_hours_desc', {
                defaultValue: 'Active window for a role.  Alerts deliver during these hours; non-critical alerts outside the window queue until shift-start.',
              })}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3">
            <div>
              <label className="block text-sm text-muted-foreground mb-1">Label</label>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                placeholder={t('forms.name_example')}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-muted-foreground mb-1">Start</label>
                <Select value={String(startHour)} onValueChange={(v) => setStartHour(Number(v))} items={HOUR_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="Start"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {HOUR_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="block text-sm text-muted-foreground mb-1">End</label>
                <Select value={String(endHour)} onValueChange={(v) => setEndHour(Number(v))} items={HOUR_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="End"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {HOUR_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <label className="block text-sm text-muted-foreground mb-1">Target Role</label>
              <Select value={targetRole} onValueChange={(v) => setTargetRole(v ?? '')} items={ROLE_ITEMS}>
                <SelectTrigger className="w-full" aria-label="Target Role"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ROLE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setShowForm(false)} disabled={saving}>
              {t('common.cancel')}
            </Button>
            <Button type="button" onClick={save} disabled={saving || !label.trim()} aria-busy={saving}>
              {saving ? 'Saving…' : editId ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm — inline base-ui Dialog. */}
      <Dialog
        open={confirmDeleteId !== null}
        onOpenChange={(open) => { if (!open) setConfirmDeleteId(null); }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this schedule?</DialogTitle>
            <DialogDescription>
              The role this schedule covers will fall back to the default (no Working Hours — alerts deliver 24/7) until you add a replacement.
            </DialogDescription>
          </DialogHeader>
          {confirmDeleteId !== null && (() => {
            const s = schedules.find(x => x.id === confirmDeleteId);
            if (!s) return null;
            return (
              <div className={`text-xs border rounded-md p-3 ${toneClasses('neutral')}`}>
                <div className="font-medium">{s.label}</div>
                <div className="opacity-80 mt-0.5">
                  {fmtHour(s.start_hour)} – {fmtHour(s.end_hour)} · {ROLE_LABELS[s.target_role] || s.target_role}
                </div>
              </div>
            );
          })()}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmDeleteId(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => { if (confirmDeleteId !== null) void remove(confirmDeleteId); }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Standalone page entrypoint kept for backward-compat with any
 * /admin/work-hours bookmark or external deep-link.  Wraps the same
 * panel the Team Management page renders.  Once the redirect in
 * router.tsx is in place this entrypoint isn't reachable from the
 * sidebar — only via direct URL — and can be deleted in a follow-up.
 */
export default function WorkHours() {
  const { t } = useTranslation();
  return (
    <div>
      <PageHeader
        icon={Clock}
        title={t('pages.work_hours_title')}
        description={t('pages.work_hours_desc_long')}
      />
      <WorkHoursPanel />
    </div>
  );
}
