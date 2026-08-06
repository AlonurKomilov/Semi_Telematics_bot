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

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { getKpiThresholds, putKpiThresholds } from './api';

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'text-foreground focus:outline-none focus:border-ring';

const FIELDS: { key: string; label: string }[] = [
  { key: 'rpm_good', label: 'RPM — good at or above ($/mi)' },
  { key: 'rpm_bad', label: 'RPM — bad below ($/mi)' },
  { key: 'empty_pct_good', label: 'Empty miles — good at or below (%)' },
  { key: 'empty_pct_bad', label: 'Empty miles — bad above (%)' },
  { key: 'gross_per_truck_good', label: 'Gross per truck — good at or above ($)' },
  { key: 'gross_per_truck_bad', label: 'Gross per truck — bad below ($)' },
];

export default function ThresholdsForm({ onSaved }: { onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // No `open` guard — the gear mounts this only while its dialog is open,
  // so mounting IS opening.
  useEffect(() => {
    setError('');
    setLoading(true);
    getKpiThresholds()
      .then((res) => {
        const v: Record<string, string> = {};
        for (const f of FIELDS) v[f.key] = String(res.thresholds[f.key] ?? '');
        setValues(v);
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
      await putKpiThresholds(out);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        What counts as good or bad for the A–D grades. Applies to the whole
        account.
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
              <input
                className={inputCls}
                type="number"
                step="0.01"
                value={values[f.key] ?? ''}
                onChange={(e) =>
                  setValues((v) => ({ ...v, [f.key]: e.target.value }))
                }
              />
            </label>
          ))}
        </div>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex justify-end">
        <Button onClick={save} disabled={saving || loading}>
          {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
          Save thresholds
        </Button>
      </div>
    </div>
  );
}
