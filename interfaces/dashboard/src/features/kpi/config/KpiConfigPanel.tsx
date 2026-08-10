/**
 * "What counts as good" — the account's KPI thresholds.
 *
 * Two-tier per metric (good / bad); grades everywhere recompute from the
 * new values on the next read.
 *
 * A FORM, not a dialog. It used to own its own `<Dialog>` and be opened
 * by a "Thresholds" button in the page header. Both are gone: KPI's
 * config now lives behind the shared `FeatureConfigGear`, which supplies
 * the dialog, so this file supplies only the body. Nesting a Dialog
 * inside the gear's Dialog would stack two focus traps and two overlays.
 *
 * Gating is the gear's job (`can_manage_config_all`) — reaching this
 * component at all means the caller holds it. The server agrees: both
 * verbs of `/kpi/config` are on the same flag.
 */

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { toneClasses } from '../../../lib/status';
import { getKpiConfig, putKpiConfig } from '../api';

  'text-foreground focus:outline-none focus:border-ring';

const FIELDS: { key: string; label: string }[] = [
  { key: 'rpm_good', label: 'RPM — good at or above ($/mi)' },
  { key: 'rpm_bad', label: 'RPM — bad below ($/mi)' },
  { key: 'empty_pct_good', label: 'Empty miles — good at or below (%)' },
  { key: 'empty_pct_bad', label: 'Empty miles — bad above (%)' },
  { key: 'gross_per_truck_good', label: 'Gross per truck — good at or above ($)' },
  { key: 'gross_per_truck_bad', label: 'Gross per truck — bad below ($)' },
];

export default function KpiConfigPanel({ onSaved }: { onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // One dirty contract for every config card on the page: chip while
  // edited, save inert until then (mirrors IncentiveEditor's).  Dirty is
  // measured against the loaded baseline, so undoing an edit returns
  // the card to pristine.
  const [dirty, setDirty] = useState(false);
  const baseline = useRef<string>('{}');

  // No `open` guard — the gear mounts this only while its dialog is open,
  // so mounting IS opening.
  useEffect(() => {
    setError('');
    setLoading(true);
    getKpiConfig()
      .then((res) => {
        // Fall back to the server's DEFAULTS, which this response has
        // always carried and this panel used to throw away.  Six blank
        // number boxes at a first-run decision point ask an owner to
        // invent "what counts as good RPM" from nothing; the shipped
        // defaults are the honest expert answer, and they are what the
        // grades are computed against until someone overrides them.
        const v: Record<string, string> = {};
        for (const f of FIELDS) {
          const stored = res.thresholds?.[f.key];
          const fallback = res.defaults?.[f.key];
          v[f.key] = String(stored ?? fallback ?? '');
        }
        setValues(v);
        baseline.current = JSON.stringify(v);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      const out: Record<string, number> = {};
      for (const [k, v] of Object.entries(values)) {
        const n = Number(v);
        if (!Number.isNaN(n)) out[k] = n;
      }
      await putKpiConfig(out);
      baseline.current = JSON.stringify(values);
      setDirty(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  return (
    // space-y-4 outer against the grid's gap-3: between-group air
    // must exceed within-group air, or the intro sentence and the six
    // fields read as one flat run of seven things.
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground max-w-prose">
        What counts as good or bad for the A–D grades. Applies to the whole
        account —{' '}
        <span className="text-foreground">saving re-grades everyone</span> on
        the next load. Values shown are the current thresholds, or the
        4truck defaults where none has been set. Grades are analytics only —
        they never change incentive pay, which comes from the rules below.
      </p>
      {loading ? (
        <div className="flex justify-center py-6">
          <Loader2 size={18} className="animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {FIELDS.map((f) => (
            <label key={f.key} className="text-sm">
              <span className="text-muted-foreground">{f.label}</span>
              {/* One numeric width step (w-28) — a full-width field
                  holding "2.3" claims 4× the size its content needs. */}
              <Input
                  type="number"
                  step="0.01"
                  className="w-28"
                  value={values[f.key] ?? ''}
                  onChange={(e) => {
                    const next = { ...values, [f.key]: e.target.value };
                    setValues(next);
                    setDirty(JSON.stringify(next) !== baseline.current);
                  }}
                />
            </label>
          ))}
        </div>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex items-center justify-end gap-3">
        {dirty && (
          <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
            Unsaved changes
          </span>
        )}
        <Button variant={dirty ? 'default' : 'outline'} onClick={save} disabled={saving || loading || !dirty}>
          {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
          Save thresholds
        </Button>
      </div>
    </div>
  );
}
