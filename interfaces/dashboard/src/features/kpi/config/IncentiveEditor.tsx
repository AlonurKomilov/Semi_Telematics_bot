/**
 * The dispatcher incentive editor — the customer's payout rules.
 *
 * Renders the three tier models the industry actually uses (a customer
 * provided all three as real spreadsheets), as ONE configurable engine:
 * pick the model, edit its rows, set the policy knobs.  Nothing here is
 * hardcoded — that was an explicit owner requirement, born from the
 * FIXED model's ambiguity ("RPM ladder says 1.5%, gross ladder says 1%:
 * which wins?") being resolved as a SETTING, not a rule we chose.
 *
 * Tier rows are a FORM-EMBEDDED LINE-ITEM EDITOR — one of the dashboard
 * rules' three sanctioned non-DataGrid table shapes.  The whole tier set
 * submits together because a ladder is one coherent value.
 *
 * The server re-validates everything through the engine
 * (features/kpi/dispatch/engine.py) — this UI's checks are conveniences,
 * not the wall.  A 422's detail is the engine's own admin-facing
 * message, so it is shown verbatim.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Check, Loader2, Plus, Wand2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../../components/ui/select';
import { Switch } from '../../../components/ui/switch';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { toneClasses } from '../../../lib/status';
import {
  getIncentivesConfig, putIncentivesConfig, putIncentiveTargets,
  type IncentiveConfig, type IncentiveTier,
} from '../api';

const EMPTY: IncentiveConfig = {
  model: 'ladder',
  combine_rule: 'lower',
  calc_cadence: 'weekly',
  calc_custom_days: null,
  exception_cap_pct: null,
  floor_weekly_gross: null,
  floor_rpm: null,
  tiers: [],
};

const MODEL_ITEMS = [
  { value: 'ladder', label: 'Ladder — gross bar + RPM steps' },
  { value: 'hybrid', label: 'Hybrid — gross range AND RPM range' },
  { value: 'fixed', label: 'Fixed — two independent ladders' },
];
const COMBINE_ITEMS = [
  { value: 'lower', label: 'Lower of the two' },
  { value: 'higher', label: 'Higher of the two' },
  { value: 'add', label: 'Added together' },
];
const CADENCE_ITEMS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'custom', label: 'Custom (days)' },
];

// A verified real-world ladder — one customer's actual rules, reproduced
// to the cent by the engine's golden test.  A STARTING POINT, offered as
// exactly that; never labelled "recommended" (we verified one carrier's
// numbers, not an industry norm).
const COMMON_LADDER: Partial<IncentiveConfig> = {
  model: 'ladder',
  exception_cap_pct: 2,
  floor_weekly_gross: 7000,
  floor_rpm: 1.9,
  tiers: [
    { min_rpm: 2.0, pct: 1.0 },
    { requires_target: true, pct: 1.0 },
    { requires_target: true, min_rpm: 2.0, pct: 1.5 },
    { requires_target: true, min_rpm: 2.5, pct: 2.0 },
  ],
};

/** A number input that treats '' as null — unset is a real state for
 *  policy knobs (no floor, no cap), not zero. */
function NumField({ value, onChange, placeholder, width = 'w-28' }: {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  placeholder?: string;
  width?: string;
}) {
  return (
    <Input
      type="number"
      step="0.01"
      className={width}
      placeholder={placeholder}
      value={value ?? ''}
      onChange={(e) => {
        const raw = e.target.value;
        onChange(raw === '' ? null : Number(raw));
      }}
    />
  );
}

function fresh(model: IncentiveConfig['model']): IncentiveTier {
  if (model === 'hybrid') {
    return { gross_min: 0, gross_max: 0, rpm_min: 0, rpm_max: 0, pct: 1 };
  }
  if (model === 'fixed') return { axis: 'rpm', min: 0, pct: 1 };
  return { pct: 1 };
}

export default function IncentiveEditor() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<IncentiveConfig>(EMPTY);
  const [configured, setConfigured] = useState(false);
  const [companies, setCompanies] = useState<{ id: number; code: string; name: string }[]>([]);
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Dirty tracking: money config with two save scopes needs to SAY when
  // either has unsaved edits.  BrowserRouter offers no useBlocker, so
  // in-app navigation cannot be intercepted — beforeunload covers tab
  // close/reload, and the visible chips cover the rest.
  const [rulesDirty, setRulesDirty] = useState(false);
  const [targetsDirty, setTargetsDirty] = useState(false);
  // A model switch clears the tier rows (the models' keys are mutually
  // exclusive; the server 422s a mixed set) — destroying hand-entered
  // rows on a dropdown change needs a confirm, not a surprise.
  const [pendingModel, setPendingModel] =
    useState<IncentiveConfig['model'] | null>(null);

  useEffect(() => {
    if (!rulesDirty && !targetsDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [rulesDirty, targetsDirty]);

  useEffect(() => {
    getIncentivesConfig()
      .then((res) => {
        if (res.config) { setCfg({ ...EMPTY, ...res.config }); setConfigured(true); }
        setCompanies(res.companies);
        const map: Record<number, string> = {};
        for (const tg of res.targets) map[tg.company_id] = String(tg.weekly_gross_target);
        setTargets(map);
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  const hasAnyTarget = Object.values(targets).some((v) => v.trim() !== '');

  const patchCfg = (patch: Partial<IncentiveConfig>) => {
    setCfg((c) => ({ ...c, ...patch }));
    setRulesDirty(true);
  };

  const setTier = (i: number, patch: Partial<IncentiveTier>) => {
    setCfg((c) => ({
      ...c,
      tiers: c.tiers.map((tier, j) => (j === i ? { ...tier, ...patch } : tier)),
    }));
    setRulesDirty(true);
  };

  const saveRules = async () => {
    setSaving(true);
    try {
      const res = await putIncentivesConfig(cfg);
      if (res.config) setCfg({ ...EMPTY, ...res.config });
      setConfigured(true);
      setRulesDirty(false);
      // THE FIRST-RUN TRAP: rules saved, targets never saved -> every
      // run pays 0% with "no target" on every row.  Saving rules is the
      // moment to say so, while the user is still on the page.
      const anyTarget = res.targets.length > 0
        || Object.values(targets).some((v) => v.trim() !== '');
      if (!anyTarget) {
        toast.warning(t('kpi_config.no_targets_warn',
          'No company has a weekly target — every run will pay 0% until targets are saved below.'));
      } else {
        toast.success(t('kpi_config.rules_saved', 'Incentive rules saved'));
      }
    } catch (e) {
      // The engine's message names the offending row — show it verbatim.
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  const saveTargets = async () => {
    setSaving(true);
    try {
      const out: Record<number, number> = {};
      for (const [cid, v] of Object.entries(targets)) {
        if (v.trim() !== '') out[Number(cid)] = Number(v);
      }
      const res = await putIncentiveTargets(out);
      const map: Record<number, string> = {};
      for (const tg of res.targets) map[tg.company_id] = String(tg.weekly_gross_target);
      setTargets(map);
      setTargetsDirty(false);
      toast.success(t('kpi_config.targets_saved', 'Company targets saved'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save targets');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 size={18} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {(!configured || !hasAnyTarget) && (
        /* The setup PATH, not a paragraph: three steps with live done-
           states, the last linking to where runs actually happen — the
           dependency between the two pages was one-directional before
           (runs 422s point here; here pointed nowhere). */
        <ol className="bg-card border border-border rounded-xl p-4 space-y-2 text-sm">
          {[
            { done: configured && cfg.tiers.length > 0,
              label: t('kpi_config.step_rules', 'Pick a model and save its tiers') },
            { done: hasAnyTarget,
              label: t('kpi_config.step_targets', 'Set each company’s weekly target') },
          ].map((step, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className={`inline-flex size-5 items-center justify-center rounded-full border text-xs ${
                step.done
                  ? 'border-transparent bg-primary text-primary-foreground'
                  : 'border-border text-muted-foreground'
              }`}>
                {step.done ? <Check size={12} /> : i + 1}
              </span>
              <span className={step.done ? 'text-muted-foreground line-through' : 'text-foreground'}>
                {step.label}
              </span>
            </li>
          ))}
          <li className="flex items-center gap-2">
            <span className="inline-flex size-5 items-center justify-center rounded-full border border-border text-xs text-muted-foreground">3</span>
            <Link to="/kpi/incentives" className="inline-flex items-center gap-1 text-primary hover:underline">
              {t('kpi_config.step_run', 'Create the first run')}
              <ArrowRight size={14} />
            </Link>
          </li>
        </ol>
      )}

      {/* ── Model + policy ──────────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 space-y-4">
        <h2 className="text-base font-semibold">
          {t('kpi_config.model_title', 'Model & policy')}
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.model', 'Tier model')}</span>
            <Select
              value={cfg.model}
              onValueChange={(v) => {
                const next = v as IncentiveConfig['model'];
                if (next === cfg.model) return;
                if (cfg.tiers.length > 0) { setPendingModel(next); return; }
                patchCfg({ model: next, tiers: [] });
              }}
              items={MODEL_ITEMS}
            >
              <SelectTrigger className="w-72" aria-label={t('kpi_config.model', 'Tier model')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {MODEL_ITEMS.map((it) => (
                  <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {cfg.model === 'fixed' && (
            <label className="text-sm space-y-1">
              <span className="block text-muted-foreground">{t('kpi_config.combine', 'When the ladders disagree')}</span>
              <Select
                value={cfg.combine_rule}
                onValueChange={(v) => patchCfg({ combine_rule: v as IncentiveConfig['combine_rule'] })}
                items={COMBINE_ITEMS}
              >
                <SelectTrigger className="w-56" aria-label={t('kpi_config.combine', 'When the ladders disagree')}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {COMBINE_ITEMS.map((it) => (
                    <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          )}
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.cadence', 'Calculated')}</span>
            <Select
              value={cfg.calc_cadence}
              onValueChange={(v) => patchCfg({ calc_cadence: v as IncentiveConfig['calc_cadence'] })}
              items={CADENCE_ITEMS}
            >
              <SelectTrigger className="w-44" aria-label={t('kpi_config.cadence', 'Calculated')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {CADENCE_ITEMS.map((it) => (
                  <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {cfg.calc_cadence === 'custom' && (
            <label className="text-sm space-y-1">
              <span className="block text-muted-foreground">{t('kpi_config.custom_days', 'Every N days')}</span>
              <NumField
                value={cfg.calc_custom_days}
                onChange={(v) => patchCfg({ calc_custom_days: v == null ? null : Math.round(v) })}
                width="w-24"
              />
            </label>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.cap', 'Exception cap (%)')}</span>
            <NumField value={cfg.exception_cap_pct} onChange={(v) => patchCfg({ exception_cap_pct: v })} placeholder="2" width="w-24" />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.floor_gross', 'Removal floor — weekly gross ($)')}</span>
            <NumField value={cfg.floor_weekly_gross} onChange={(v) => patchCfg({ floor_weekly_gross: v })} placeholder="7000" />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.floor_rpm', 'Removal floor — RPM ($/mi)')}</span>
            <NumField value={cfg.floor_rpm} onChange={(v) => patchCfg({ floor_rpm: v })} placeholder="1.9" width="w-24" />
          </label>
        </div>
        <p className="text-xs text-muted-foreground">
          {t('kpi_config.floor_note',
            'A truck below BOTH floors pays 0% — the “removed from dispatcher” rule. Manual exceptions above the cap are refused, never clamped.')}
        </p>
      </section>

      {/* ── Tier rows ───────────────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">{t('kpi_config.tiers_title', 'Tiers')}</h2>
          <Button size="sm" variant="outline" onClick={() => { setCfg((c) => ({ ...c, tiers: [...c.tiers, fresh(c.model)] })); setRulesDirty(true); }}>
            <Plus size={14} /> {t('kpi_config.add_tier', 'Add tier')}
          </Button>
        </div>
        {cfg.tiers.length === 0 ? (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              {t('kpi_config.no_tiers', 'No tiers yet — every truck resolves to 0% until at least one tier exists.')}
            </p>
            <Button size="sm" variant="outline"
              onClick={() => { patchCfg({ ...COMMON_LADDER }); }}>
              <Wand2 size={14} className="mr-1.5" />
              {t('kpi_config.preset', 'Start from a common ladder')}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t('kpi_config.preset_note',
                'A real carrier’s ladder (1% / 1.5% / 2% with a 2% exception cap and $7,000 / 1.9 RPM floor) — a starting point to edit, not a recommendation.')}
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {cfg.tiers.map((tier, i) => (
              <li key={i} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                {cfg.model === 'ladder' && (
                  <>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <Switch
                        checked={!!tier.requires_target}
                        onCheckedChange={(v) => setTier(i, { requires_target: v })}
                        aria-label={t('kpi_config.requires_target', 'Requires target met')}
                      />
                      {t('kpi_config.target_met', 'target met')}
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.min_gross', 'weekly gross ≥')}
                      <NumField value={tier.min_weekly_gross} onChange={(v) => setTier(i, { min_weekly_gross: v })} placeholder="—" width="w-24" />
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.min_rpm', 'RPM ≥')}
                      <NumField value={tier.min_rpm} onChange={(v) => setTier(i, { min_rpm: v })} placeholder="—" width="w-20" />
                    </label>
                  </>
                )}
                {cfg.model === 'hybrid' && (
                  <>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.gross', 'gross')}
                      <NumField value={tier.gross_min} onChange={(v) => setTier(i, { gross_min: v ?? 0 })} width="w-24" />
                      –
                      <NumField value={tier.gross_max} onChange={(v) => setTier(i, { gross_max: v ?? 0 })} width="w-24" />
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.rpm', 'RPM')}
                      <NumField value={tier.rpm_min} onChange={(v) => setTier(i, { rpm_min: v ?? 0 })} width="w-20" />
                      –
                      <NumField value={tier.rpm_max} onChange={(v) => setTier(i, { rpm_max: v ?? 0 })} width="w-20" />
                    </label>
                  </>
                )}
                {cfg.model === 'fixed' && (
                  <>
                    <Select
                      value={tier.axis ?? 'rpm'}
                      onValueChange={(v) => setTier(i, { axis: v as 'rpm' | 'gross' })}
                      items={[{ value: 'rpm', label: 'RPM' }, { value: 'gross', label: 'Gross' }]}
                    >
                      <SelectTrigger className="w-28" aria-label={t('kpi_config.axis', 'Axis')}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="rpm">RPM</SelectItem>
                        <SelectItem value="gross">Gross</SelectItem>
                      </SelectContent>
                    </Select>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      ≥
                      <NumField value={tier.min} onChange={(v) => setTier(i, { min: v ?? 0 })} width="w-24" />
                    </label>
                  </>
                )}
                <label className="inline-flex items-center gap-1.5 text-muted-foreground ml-auto">
                  →
                  <NumField value={tier.pct} onChange={(v) => setTier(i, { pct: v ?? 0 })} width="w-20" />
                  %
                </label>
                <button
                  type="button"
                  onClick={() => { setCfg((c) => ({ ...c, tiers: c.tiers.filter((_, j) => j !== i) })); setRulesDirty(true); }}
                  aria-label={t('kpi_config.remove_tier', 'Remove tier')}
                  className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* One save for Model & policy + Tiers — they are ONE PUT, and a
          button living inside the Tiers card claimed a narrower scope
          than it had (the audit's grammar rule: a Save button's card is
          its scope).  The label names everything it commits. */}
      <div className="flex items-center justify-end gap-3">
        {rulesDirty && (
          <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
            {t('kpi_config.unsaved', 'Unsaved changes')}
          </span>
        )}
        <Button onClick={saveRules} disabled={saving}>
          {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
          {t('kpi_config.save_rules', 'Save model, policy & tiers')}
        </Button>
      </div>

      {/* ── Per-company weekly targets ──────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 space-y-3">
        <h2 className="text-base font-semibold">{t('kpi_config.targets_title', 'Weekly gross targets')}</h2>
        <p className="text-xs text-muted-foreground">
          {t('kpi_config.targets_note',
            'One bar per company. The bar prorates by active days — $8,000/week over 19 active days is a $21,714.29 target. A company without a bar resolves 0% until one is set.')}
        </p>
        <ul className="divide-y divide-border">
          {companies.map((co) => (
            <li key={co.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="text-foreground">{co.name} <span className="text-muted-foreground">({co.code})</span></span>
              <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                $
                <Input
                  type="number"
                  step="100"
                  className="w-28"
                  value={targets[co.id] ?? ''}
                  onChange={(e) => { setTargets((m) => ({ ...m, [co.id]: e.target.value })); setTargetsDirty(true); }}
                  placeholder="8000"
                />
                {t('kpi_config.per_week', '/week')}
              </label>
            </li>
          ))}
        </ul>
        <div className="flex items-center justify-end gap-3">
          {targetsDirty && (
            <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
              {t('kpi_config.unsaved', 'Unsaved changes')}
            </span>
          )}
          <Button onClick={saveTargets} disabled={saving}>
            {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_config.save_targets', 'Save targets')}
          </Button>
        </div>
      </section>
      <Dialog open={pendingModel != null}
        onOpenChange={(o) => { if (!o) setPendingModel(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {t('kpi_config.switch_title', 'Switch model and clear tiers?')}
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_config.switch_body',
              'The {{count}} tier(s) you entered belong to the current model and cannot carry over — the models use different fields. Switching clears them.',
              { count: cfg.tiers.length })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingModel(null)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={() => {
              if (pendingModel) patchCfg({ model: pendingModel, tiers: [] });
              setPendingModel(null);
            }}>
              {t('kpi_config.switch_confirm', 'Switch & clear')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
