import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X, Trash2, Plus } from 'lucide-react';
import { apiJSON } from '../../api/client';
import type { MaintenanceTemplate } from '../../types';
import { PRIORITY_OPTIONS } from './badges';
import ServiceTaskPicker from '../service-tasks/ServiceTaskPicker';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

// Priority options carry lowercase wire values; title-case the display
// label so the picker reads "Low / Medium / …" (matches the old
// CSS-``capitalize`` presentation).
const PRIORITY_ITEMS = PRIORITY_OPTIONS.map((p) => ({
  value: p,
  label: p.charAt(0).toUpperCase() + p.slice(1),
}));

interface Props {
  onClose: () => void;
  // Notify the parent so it can refresh its template-picker dropdown.
  onChange?: () => void;
}

// Lightweight CRUD modal for maintenance templates.  Intentionally
// simpler than the full task drawer — templates aren't tracked through
// completion / attestation / recurrence chains, they're just a
// re-usable seed for new tasks.  Edit-in-place is omitted; the
// expected workflow is "delete + recreate" since templates are tiny.
export function TemplatesModal({ onClose, onChange }: Props) {
  const { has: hasPerm } = useViewPermissions();
  const canCreateTasks = hasPerm('can_service_tasks');
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-templates'],
    queryFn: () => apiJSON<{ templates: MaintenanceTemplate[] }>(
      '/maintenance/templates',
    ),
  });
  const templates = data?.templates ?? [];

  // Same single-trigger pattern the task forms use (Tasks.tsx).
  // One Due-by mode + one Repeat checkbox keeps the cognitive load
  // identical across Create Task, Edit Task, and template authoring.
  type TriggerMode = 'date' | 'miles' | 'hours';

  // New-template form state — kept inside the modal so the parent
  // doesn't have to track partial template-creation state across
  // unrelated user actions.
  const [showCreate, setShowCreate] = useState(false);
  const [nName, setNName] = useState('');
  const [nType, setNType] = useState('inspection');
  const [nDesc, setNDesc] = useState('');
  const [nPriority, setNPriority] = useState('medium');
  const [nTriggerMode, setNTriggerMode] = useState<TriggerMode>('date');
  const [nTriggerValue, setNTriggerValue] = useState('');
  const [nRepeat, setNRepeat] = useState(false);
  const [saving, setSaving] = useState(false);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['maintenance-templates'] });
    onChange?.();
  };

  const reset = () => {
    setNName(''); setNType('inspection'); setNDesc(''); setNPriority('medium');
    setNTriggerMode('date'); setNTriggerValue(''); setNRepeat(false);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!nName.trim()) {
      toast.error('Template name is required.');
      return;
    }
    if (!nTriggerValue.trim()) {
      toast.error('Set a value for the chosen trigger.');
      return;
    }
    const n = Number(nTriggerValue);
    if (!Number.isFinite(n) || n <= 0) {
      toast.error('Trigger value must be a positive number.');
      return;
    }
    // Map the single-trigger pick to the API's three optional fields.
    // Only the chosen dimension is sent; the others stay undefined so
    // the template ships with a single coherent trigger.
    const due_in_days   = nTriggerMode === 'date'  ? n : undefined;
    const due_in_miles  = nTriggerMode === 'miles' ? n : undefined;
    const due_in_hours  = nTriggerMode === 'hours' ? n : undefined;
    const recur_interval_days =
      nRepeat && nTriggerMode === 'date'  ? n : undefined;
    const recur_interval_miles =
      nRepeat && nTriggerMode === 'miles' ? n : undefined;
    const recur_interval_engine_hours =
      nRepeat && nTriggerMode === 'hours' ? n : undefined;
    setSaving(true);
    try {
      await apiJSON('/maintenance/templates', { method: 'POST', body: {
        name: nName.trim(),
        task_type: nType,
        description: nDesc,
        priority: nPriority,
        due_in_days,
        due_in_miles,
        due_in_hours,
        recur_interval_days,
        recur_interval_miles,
        recur_interval_engine_hours,
      }});
      toast.success('Template saved');
      reset();
      setShowCreate(false);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (t: MaintenanceTemplate) => {
    const ok = window.confirm(
      `Delete the "${t.name}" template?\n\nThis can't be undone, but existing tasks created from it are unaffected.`,
    );
    if (!ok) return;
    try {
      await apiJSON('/maintenance/templates/' + t.id, { method: 'DELETE' });
      toast.success('Template deleted');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex justify-center items-start pt-12"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl max-h-[80vh] bg-card border border-border rounded-xl p-6 overflow-y-auto shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">Task Templates</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Re-usable defaults for the common service intervals.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1">
            <X size={16} />
          </button>
        </div>

        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

        {!isLoading && templates.length === 0 && !showCreate && (
          <div className="text-sm text-muted-foreground bg-muted/40 border border-border rounded p-4 mb-4">
            No templates yet. Add one so the next time you create a task you
            can pick it from a dropdown instead of refilling every field.
          </div>
        )}

        {templates.length > 0 && (
          <ul className="space-y-2 mb-4">
            {templates.map(t => (
              <li
                key={t.id}
                className="flex items-start gap-3 p-3 bg-muted/40 border border-border rounded-lg"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">
                    {t.name}
                    <span className="text-muted-foreground font-normal ml-2 text-xs capitalize">
                      {t.task_type.replace(/_/g, ' ')} · {t.priority}
                    </span>
                  </p>
                  {t.description && (
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                      {t.description}
                    </p>
                  )}
                  <p className="text-2xs text-muted-foreground mt-1">
                    {[
                      t.due_in_days   ? `due in ${t.due_in_days} days` : null,
                      t.due_in_miles  ? `${Number(t.due_in_miles).toLocaleString()} mi` : null,
                      t.due_in_hours  ? `${Number(t.due_in_hours).toLocaleString()} h` : null,
                      t.recur_interval_days ? `repeats every ${t.recur_interval_days}d` : null,
                      t.recur_interval_miles ? `${Number(t.recur_interval_miles).toLocaleString()}mi` : null,
                      t.recur_interval_engine_hours ? `${Number(t.recur_interval_engine_hours).toLocaleString()}h` : null,
                    ].filter(Boolean).join(' · ') || 'no triggers set'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(t)}
                  className="text-muted-foreground hover:text-destructive p-1"
                  aria-label={`Delete ${t.name}`}
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}

        {!showCreate ? (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 text-primary-foreground rounded-md text-xs font-medium transition"
          >
            <Plus size={14} />
            New template
          </button>
        ) : (
          <form
            onSubmit={handleCreate}
            className="grid grid-cols-2 gap-2 p-3 bg-muted/30 border border-border rounded"
          >
            <label className="col-span-2 block">
              <span className="block text-xs text-muted-foreground mb-1">Name</span>
              <input
                required
                value={nName}
                onChange={e => setNName(e.target.value)}
                placeholder="e.g. Oil change (5k mi or 6 months)"
                className="w-full bg-card border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
              />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">Service task</span>
              {/* The same picker the add/edit forms use — a template
                  that offers a different task list than the form it
                  fills is how vocabularies fork. */}
              <ServiceTaskPicker value={nType} onChange={setNType} canCreate={canCreateTasks} />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">Priority</span>
              <Select value={nPriority} onValueChange={setNPriority} items={PRIORITY_ITEMS}>
                <SelectTrigger className="w-full" aria-label="Priority"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PRIORITY_ITEMS.map(it => (
                    <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="col-span-2 block">
              <span className="block text-xs text-muted-foreground mb-1">Description</span>
              <input
                value={nDesc}
                onChange={e => setNDesc(e.target.value)}
                placeholder="e.g. Full synthetic 15W-40 + filter"
                className="w-full bg-card border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
              />
            </label>
            {/* Same single-trigger pattern the task forms use —
                Due-by tabs + one period input + one Repeat checkbox.
                Templates carry an interval (days/miles/hours) rather
                than an absolute target, so the placeholders read
                "e.g. 90 / 5000 / 500" depending on mode. */}
            <div className="col-span-2">
              <span className="block text-xs text-muted-foreground mb-1">Due by</span>
              <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md mb-2" role="group" aria-label="Due by">
                {([
                  { k: 'date',  label: 'Date'  },
                  { k: 'miles', label: 'Miles' },
                  { k: 'hours', label: 'Hours' },
                ] as const).map(opt => {
                  const active = nTriggerMode === opt.k;
                  return (
                    <button
                      key={opt.k}
                      type="button"
                      onClick={() => setNTriggerMode(opt.k)}
                      aria-pressed={active}
                      className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                        active
                          ? 'bg-card text-foreground shadow-sm'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              <input
                type="number"
                min="1"
                value={nTriggerValue}
                onChange={e => setNTriggerValue(e.target.value)}
                placeholder={
                  nTriggerMode === 'date'  ? 'e.g. 90 days'
                  : nTriggerMode === 'miles' ? 'e.g. 5000 miles'
                  : 'e.g. 500 hours'
                }
                className="w-full bg-card border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
              />
            </div>
            <label className="col-span-2 inline-flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={nRepeat}
                onChange={e => setNRepeat(e.target.checked)}
                className="accent-primary cursor-pointer"
              />
              <span className="text-foreground">
                {(() => {
                  const unit = nTriggerMode === 'date' ? 'days' : nTriggerMode === 'miles' ? 'mi' : 'h';
                  return nTriggerValue
                    ? <>Repeat every {Number(nTriggerValue).toLocaleString()} {unit} after completion</>
                    : <>Repeat after completion</>;
                })()}
              </span>
            </label>
            <div className="col-span-2 flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => { setShowCreate(false); reset(); }}
                className="px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded text-xs font-medium"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="flex-1 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-xs font-medium text-primary-foreground transition"
              >
                {saving ? 'Saving…' : 'Save template'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
