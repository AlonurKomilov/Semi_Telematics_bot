import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Boxes } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses } from '../../lib/status';
import { TOGGLEABLE_MODULES } from '../../config/featureCatalog';

interface ModulesData { enabled: string[]; all: string[] }

/**
 * Owner "Modules" page — turn departments (Fleet / Dispatch / Safety /
 * HR / Accounting) on or off for the whole account.  Disabling one hides
 * its features from every persona's sidebar (Sidebar filters via the
 * feature catalog).  Core + Account admin are always on.  All free for
 * now — billing/per-module pricing comes later.
 */
export default function Modules() {
  const { refreshUser } = useAuth();
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const { data, isLoading, error: qErr } = useQuery({
    queryKey: ['account-modules'],
    queryFn: () => apiJSON<ModulesData>('/admin/account/modules'),
  });

  useEffect(() => { if (data) setEnabled(new Set(data.enabled)); }, [data]);

  const serverEnabled = new Set(data?.enabled ?? []);
  const dirty = !!data && (
    enabled.size !== serverEnabled.size || [...enabled].some((m) => !serverEnabled.has(m))
  );

  function toggle(id: string) {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setSuccess('');
  }

  async function save() {
    setSaving(true); setError('');
    try {
      await apiJSON('/admin/account/modules', { method: 'PUT', body: { enabled: [...enabled] } });
      setSuccess('Modules updated — your sidebar reflects the change.');
      await refreshUser();  // refresh /user/me so the sidebar re-filters
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally { setSaving(false); }
  }

  return (
    <div className="max-w-2xl pb-20">
      <PageHeader
        icon={Boxes}
        title="Modules"
        description="Turn departments on or off for your account. Disabling one hides its features from every sidebar. Core features (Live Map, Vehicles, Alerts, Reports…) and account admin always stay on."
      />

      {error && <p className={`text-sm rounded-md px-3 py-2 mb-3 ${toneClasses('danger')}`}>{error}</p>}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      {qErr ? (
        <p className="text-danger text-sm">{qErr instanceof Error ? qErr.message : 'Failed to load modules'}</p>
      ) : isLoading || !data ? (
        <CardSkeleton />
      ) : (
        <div className="space-y-2">
          {TOGGLEABLE_MODULES.map((m) => {
            const on = enabled.has(m.id);
            const Icon = m.icon;
            return (
              <div key={m.id} className="flex items-center gap-3 bg-card border border-border rounded-lg p-3">
                <span className={`inline-flex items-center justify-center w-9 h-9 rounded-lg shrink-0 ${on ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                  <Icon size={18} />
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold">{m.label}</div>
                  <div className="text-xs text-muted-foreground">{m.blurb}</div>
                </div>
                <button
                  onClick={() => toggle(m.id)}
                  role="switch"
                  aria-checked={on}
                  aria-label={`${m.label} module`}
                  className={`relative w-10 h-6 rounded-full transition shrink-0 ${on ? 'bg-primary' : 'bg-muted'}`}
                >
                  <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-background shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {dirty && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-popover border-t border-border px-4 py-3 flex items-center justify-end gap-2 shadow-lg">
          <button onClick={() => setEnabled(new Set(data!.enabled))} className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground">Discard</button>
          <button onClick={save} disabled={saving} className="px-4 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition disabled:opacity-50">
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}
    </div>
  );
}
