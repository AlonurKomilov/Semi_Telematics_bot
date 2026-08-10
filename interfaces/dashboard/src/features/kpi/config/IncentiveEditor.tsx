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
import { useEffect, useRef, useState } from 'react';
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

/** Targets normalised for dirty comparison: an entry typed then cleared
 *  back to empty is the same as never touched. */
function normTargets(m: Record<number, string>): string {
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(m).filter(([, v]) => v.trim() !== ''),
    ),
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
  // Dirty is measured AGAINST A BASELINE, not set-and-forgotten: undoing
  // an edit back to the loaded state returns the card to pristine.
  const [rulesDirty, setRulesDirty] = useState(false);
  const [targetsDirty, setTargetsDirty] = useState(false);
  const rulesBaseline = useRef<string>(JSON.stringify(EMPTY));
  const targetsBaseline = useRef<string>('{}');
  // A model switch clears the tier rows (the models' keys are mutually
  // exclusive; the server 422s a mixed set) — destroying hand-entered
  // rows on a dropdown change needs a confirm, not a surprise.
  const [pendingModel, setPendingModel] =
    useState<IncentiveConfig['model'] | null>(null);
  // Saving an EMPTY tier list over a previously saved ladder wipes it —
  // same class of surprise as the model switch, same answer: confirm.
  const [emptySaveOpen, setEmptySaveOpen] = useState(false);

  useEffect(() => {
    if (!rulesDirty && !targetsDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [rulesDirty, targetsDirty]);

  useEffect(() => {
    getIncentivesConfig()
      .then((res) => {
        if (res.config) {
          const loaded = { ...EMPTY, ...res.config };
          setCfg(loaded);
          setConfigured(true);
          rulesBaseline.current = JSON.stringify(loaded);
        }
        setCompanies(res.companies);
        const map: Record<number, string> = {};
        for (const tg of res.targets) map[tg.company_id] = String(tg.weekly_gross_target);
        setTargets(map);
        targetsBaseline.current = normTargets(map);
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  const hasAnyTarget = Object.values(targets).some((v) => v.trim() !== '');

  const setCfgDirty = (next: IncentiveConfig) => {
    setCfg(next);
    setRulesDirty(JSON.stringify(next) !== rulesBaseline.current);
  };

  const patchCfg = (patch: Partial<IncentiveConfig>) => {
    setCfgDirty({ ...cfg, ...patch });
  };

  const setTier = (i: number, patch: Partial<IncentiveTier>) => {
    setCfgDirty({
      ...cfg,
      tiers: cfg.tiers.map((tier, j) => (j === i ? { ...tier, ...patch } : tier)),
    });
  };

  const setTargetsField = (companyId: number, value: string) => {
    const next = { ...targets, [companyId]: value };
    setTargets(next);
    setTargetsDirty(normTargets(next) !== targetsBaseline.current);
  };

  const requestSaveRules = () => {
    if (configured && cfg.tiers.length === 0) {
      setEmptySaveOpen(true);
      return;
    }
    void saveRules();
  };

  const saveRules = async () => {
    setEmptySaveOpen(false);
    setSaving(true);
    try {
      const res = await putIncentivesConfig(cfg);
      const saved = res.config ? { ...EMPTY, ...res.config } : cfg;
      setCfg(saved);
      setConfigured(true);
      rulesBaseline.current = JSON.stringify(saved);
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
      targetsBaseline.current = normTargets(map);
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
    <div className="space-y-8">
      {(!configured || !hasAnyTarget) && (
        /* The setup PATH, not a paragraph: three steps with live done-
           states, the last linking to where runs actually happen — the
           dependency between the two pages was one-directional before
           (runs 422s point here; here pointed nowhere). */
        /* Guidance plane, not a config region — a perceptible tint and
           NO border: the 1px border + radius grammar stays reserved for
           editable workspace cards.  The heading scopes the strip to
           INCENTIVES: grading thresholds above are a separate,
           display-only product and deliberately not a step here. */
        <div className="bg-muted rounded-xl p-5 space-y-2 text-sm">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('kpi_config.setup_title', 'Incentive setup')}
          </h3>
          <ol className="space-y-2">
            {(() => {
              const steps = [
                { done: configured && cfg.tiers.length > 0,
                  label: t('kpi_config.step_rules', 'Pick a model and save its tiers') },
                { done: hasAnyTarget,
                  label: t('kpi_config.step_targets', 'Set each company’s weekly target') },
              ];
              const current = steps.findIndex((s) => !s.done);
              return steps.map((step, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span className={`inline-flex size-5 items-center justify-center rounded-full border text-xs ${
                    step.done
                      ? 'border-transparent bg-primary text-primary-foreground'
                      : i === current
                        ? 'border-primary text-primary'
                        : 'border-border text-muted-foreground'
                  }`}>
                    {step.done ? <Check size={12} /> : i + 1}
                  </span>
                  <span className={step.done
                    ? 'text-muted-foreground line-through'
                    : i === current ? 'text-foreground font-medium' : 'text-muted-foreground'}>
                    {step.label}
                  </span>
                </li>
              ));
            })()}
            <li className="flex items-center gap-2">
              <span className="inline-flex size-5 items-center justify-center rounded-full border border-border text-xs text-muted-foreground">3</span>
              {/* The one clickable row — underlined always (text-primary
                  alone is near-black in this theme), padded to a ≥24px
                  hit box without moving the 28px row rhythm. */}
              <Link to="/kpi/incentives"
                className="inline-flex items-center gap-1 py-1 -my-1 text-primary underline underline-offset-4 hover:no-underline">
                {t('kpi_config.step_run', 'Create the first run')}
                <ArrowRight size={14} />
              </Link>
            </li>
          </ol>
        </div>
      )}

      {/* ── Incentive rules: model + policy + tiers ─────────────────
          ONE card because they are ONE PUT — the save's enclosure is
          its scope, and a save floating between two cards belongs to
          neither.  Sub-sections carry caps labels. */}
      <section className="bg-card border border-border rounded-xl p-5 space-y-4">
        <h2 className="text-base font-semibold">
          {t('kpi_config.rules_title', 'Incentive rules')}
        </h2>
        {/* Both sub-sections get the SAME anatomy: a hairline seam +
            padding — one seam rule, and the title band above is bounded
            by a line, not by air alone. */}
        <div className="border-t border-border pt-4 space-y-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t('kpi_config.model_title', 'Model & policy')}
        </h3>
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
        {/* Fixed w-56 cells: the three same-class numeric fields form a
            column track independent of label length, and one input
            width step (w-28) for all of them. */}
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm space-y-1 w-56">
            <span className="block text-muted-foreground">{t('kpi_config.cap', 'Exception cap (%)')}</span>
            {/* Word placeholders, not example numbers: empty IS a real
                state (no cap / no floor), and a grey "7000" reads as a
                filled value at a glance. */}
            <NumField value={cfg.exception_cap_pct} onChange={(v) => patchCfg({ exception_cap_pct: v })} placeholder={t('kpi_config.none', 'none')} />
          </label>
          <label className="text-sm space-y-1 w-56">
            <span className="block text-muted-foreground">{t('kpi_config.floor_gross', 'Removal floor — weekly gross ($)')}</span>
            <NumField value={cfg.floor_weekly_gross} onChange={(v) => patchCfg({ floor_weekly_gross: v })} placeholder={t('kpi_config.none', 'none')} />
          </label>
          <label className="text-sm space-y-1 w-56">
            <span className="block text-muted-foreground">{t('kpi_config.floor_rpm', 'Removal floor — RPM ($/mi)')}</span>
            <NumField value={cfg.floor_rpm} onChange={(v) => patchCfg({ floor_rpm: v })} placeholder={t('kpi_config.none', 'none')} />
          </label>
        </div>
        <p className="text-xs text-muted-foreground max-w-prose">
          {t('kpi_config.floor_note',
            'A truck below BOTH floors pays 0% — the “removed from dispatcher” rule. Manual exceptions above the cap are refused, never clamped.')}
        </p>
        </div>

        {/* ── Tier rows (sub-section of the same card/PUT) ───────── */}
        <div className="border-t border-border pt-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('kpi_config.tiers_title', 'Tiers')}</h3>
            {/* The resolution rule, stated where the rows are edited —
                the engine pays the HIGHEST matching row, not the first. */}
            <p className="text-xs text-muted-foreground mt-1 max-w-prose">
              {t('kpi_config.tiers_hint',
                'A truck earns every row whose conditions it meets — the highest % pays. An empty condition means no requirement; “weekly target met” compares against the company target set below.')}
            </p>
          </div>
          {/* The only action that advances an unconfigured account —
              filled, so it out-weighs the (inert) saves below. */}
          <Button size="sm" onClick={() => setCfgDirty({ ...cfg, tiers: [...cfg.tiers, fresh(cfg.model)] })}>
            <Plus size={14} /> {t('kpi_config.add_tier', 'Add tier')}
          </Button>
        </div>
        {cfg.tiers.length === 0 ? (
          /* The page's one destination region keeps a BOUNDED well while
             empty — a dashed enclosure with real area, not a bare
             sentence a future row-list will replace. */
          <div className="rounded-lg border border-dashed border-border p-4 min-h-24 space-y-2">
            <p className="text-sm text-muted-foreground max-w-prose">
              {t('kpi_config.no_tiers', 'No tiers yet — runs cannot be created until at least one tier exists.')}
            </p>
            <Button size="sm" variant="outline"
              onClick={() => {
                patchCfg({ ...COMMON_LADDER });
                // The preset also fills cap + floors in the sub-section
                // ABOVE the user's gaze — say so, or it looks unfilled.
                toast.success(t('kpi_config.preset_applied',
                  '4 tiers added; the 2% exception cap and $7,000 / 1.9 RPM floors are filled in Model & policy above.'));
              }}>
              <Wand2 size={14} className="mr-1.5" />
              {t('kpi_config.preset', 'Start from a common ladder')}
            </Button>
            <p className="text-xs text-muted-foreground max-w-prose">
              {t('kpi_config.preset_note',
                'A real carrier’s ladder: 1% for RPM ≥ 2.0 even below target, 1% at target, 1.5% at target + RPM ≥ 2.0, 2% at target + RPM ≥ 2.5 — plus a 2% exception cap and $7,000 / 1.9 RPM floors. A starting point to edit, not a recommendation.')}
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
                        aria-label={t('kpi_config.requires_target', 'Requires weekly target met')}
                      />
                      {t('kpi_config.target_met', 'weekly target met')}
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.min_gross', 'weekly gross ≥')}
                      <NumField value={tier.min_weekly_gross} onChange={(v) => setTier(i, { min_weekly_gross: v })} placeholder={t('kpi_config.any', 'any')} width="w-24" />
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.min_rpm', 'RPM ≥')}
                      <NumField value={tier.min_rpm} onChange={(v) => setTier(i, { min_rpm: v })} placeholder={t('kpi_config.any', 'any')} width="w-20" />
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
                {/* The row's OUTPUT + its remove control wrap as ONE
                    unit — flex-wrap must never strand the % on its own
                    line away from the X. */}
                <span className="ml-auto flex items-center gap-3">
                  <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                    →
                    <NumField value={tier.pct} onChange={(v) => setTier(i, { pct: v ?? 0 })} width="w-20" />
                    %
                  </label>
                  <button
                    type="button"
                    onClick={() => setCfgDirty({ ...cfg, tiers: cfg.tiers.filter((_, j) => j !== i) })}
                    aria-label={t('kpi_config.remove_tier', 'Remove tier')}
                    className="p-1.5 rounded text-muted-foreground hover:text-danger hover:bg-muted"
                  >
                    <X size={14} />
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        </div>

        {/* One save for the whole card — model, policy and tiers are
            ONE PUT, so the button sits inside the one card that IS its
            scope and takes the CARD'S name: all three saves on the page
            read "Save <card title>" (same grammar as Targets below). */}
        <div className="flex items-center justify-end gap-3">
          {rulesDirty && (
            <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
              {t('kpi_config.unsaved', 'Unsaved changes')}
            </span>
          )}
          {/* Weight tracks actionability: outline while inert, filled
              the moment there is something to save. */}
          <Button variant={rulesDirty ? 'default' : 'outline'} onClick={requestSaveRules} disabled={saving || !rulesDirty}>
            {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_config.save_rules', 'Save incentive rules')}
          </Button>
        </div>
      </section>

      {/* ── Per-company weekly targets ──────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 space-y-3">
        <h2 className="text-base font-semibold">{t('kpi_config.targets_title', 'Weekly gross targets')}</h2>
        <p className="text-xs text-muted-foreground max-w-prose">
          {t('kpi_config.targets_note',
            'One weekly target per company — the same target the tiers’ “weekly target met” checks against. It prorates by active days: $8,000/week over 19 active days is a $21,714.29 target. A company without one resolves 0% until it is set.')}
        </p>
        {/* border-t: the header+description band ends at a line, so the
            first company row cannot read as part of the header group. */}
        <ul className="divide-y divide-border border-t border-border">
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
                  onChange={(e) => setTargetsField(co.id, e.target.value)}
                  placeholder={t('kpi_config.no_target_ph', 'no target')}
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
          <Button variant={targetsDirty ? 'default' : 'outline'} onClick={saveTargets} disabled={saving || !targetsDirty}>
            {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_config.save_targets', 'Save targets')}
          </Button>
        </div>
      </section>
      <Dialog open={emptySaveOpen}
        onOpenChange={(o) => { if (!o) setEmptySaveOpen(false); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {t('kpi_config.empty_save_title', 'Save with no tiers?')}
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_config.empty_save_body',
              'This removes every saved tier. New incentive runs cannot be created until tiers exist again; existing runs keep their own snapshot and are not affected.')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEmptySaveOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={() => { void saveRules(); }} disabled={saving}>
              {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_config.empty_save_confirm', 'Save with no tiers')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
