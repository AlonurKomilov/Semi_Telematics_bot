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

// One ROW per metric: the good/bad pair sits ADJACENT (they are the
// values being compared), the metric name renders once, the unit once
// at the row's end.  The old 2-col grid put a pair 493px apart while
// stacking different metrics 64px apart — geometry against meaning.
const METRICS: {
  label: string; unit: string; prefix?: boolean;
  goodKey: string; goodLabel: string; badKey: string; badLabel: string;
}[] = [
  { label: 'RPM', unit: '$/mi',
    goodKey: 'rpm_good', goodLabel: 'good ≥',
    badKey: 'rpm_bad', badLabel: 'bad <' },
  { label: 'Empty miles', unit: '%',
    goodKey: 'empty_pct_good', goodLabel: 'good ≤',
    badKey: 'empty_pct_bad', badLabel: 'bad >' },
  { label: 'Gross per truck', unit: '$', prefix: true,
    goodKey: 'gross_per_truck_good', goodLabel: 'good ≥',
    badKey: 'gross_per_truck_bad', badLabel: 'bad <' },
];
const FIELD_KEYS = METRICS.flatMap((m) => [m.goodKey, m.badKey]);

export default function KpiConfigPanel({ onSaved, onDirtyChange }: {
  onSaved: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
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
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

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
        for (const key of FIELD_KEYS) {
          const stored = res.thresholds?.[key];
          const fallback = res.defaults?.[key];
          v[key] = String(stored ?? fallback ?? '');
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
      <p className="text-xs text-muted-foreground max-w-prose">
        What counts as good or bad for the A–D grades. Applies to the whole
        account —{' '}
        <span className="text-foreground">saving re-grades everyone</span> on
        the next load. Values shown are the current thresholds, or the
        4truck defaults where none has been set. Grades are analytics only —
        they never change incentive pay, which comes from the rules below.
        {' '}How the letter is made: between good and bad counts as neutral;
        {' '}<span className="text-foreground">A</span> = every metric good ·{' '}
        <span className="text-foreground">B</span> = mixed, none bad ·{' '}
        <span className="text-foreground">C</span> = one metric bad ·{' '}
        <span className="text-foreground">D</span> = two bad, or negative gross.
      </p>
      {loading ? (
        <div className="flex justify-center py-6">
          <Loader2 size={18} className="animate-spin text-muted-foreground" />
        </div>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {METRICS.map((m) => (
            <li key={m.goodKey} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <span className="w-32 text-foreground">{m.label}</span>
              {([['good', m.goodKey, m.goodLabel], ['bad', m.badKey, m.badLabel]] as const).map(
                ([side, key, sideLabel]) => (
                  <label key={side} className="inline-flex items-center gap-1.5 text-muted-foreground">
                    {sideLabel}
                    {/* Reserved affix slot — input x aligns whether or
                        not the metric carries a $ prefix. */}
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-3 text-muted-foreground">{m.prefix ? '$' : ''}</span>
                      <Input
                        type="number"
                        step="0.01"
                        className="w-28"
                        aria-label={`${m.label} — ${sideLabel}`}
                        value={values[key] ?? ''}
                        onChange={(e) => {
                          const next = { ...values, [key]: e.target.value };
                          setValues(next);
                          setDirty(JSON.stringify(next) !== baseline.current);
                        }}
                      />
                    </span>
                  </label>
                ))}
              <span className="ml-auto text-xs text-muted-foreground">{m.unit}</span>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
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
