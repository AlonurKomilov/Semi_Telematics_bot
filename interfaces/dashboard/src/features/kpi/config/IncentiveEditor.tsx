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

import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { toneClasses, toneText } from '../../../lib/status';
import {
  getIncentivesConfig, previewIncentiveRules, putIncentivesConfig,
  putIncentiveTargets,
  type IncentiveConfig, type IncentiveTier, type RulesPreview,
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
  { value: 'ladder', label: 'Ladder — target bar + RPM steps' },
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
function NumField({ value, onChange, placeholder, width = 'w-28', ariaLabel }: {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  placeholder?: string;
  width?: string;
  ariaLabel?: string;
}) {
  return (
    <Input
      type="number"
      step="0.01"
      className={width}
      aria-label={ariaLabel}
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

export default function IncentiveEditor({ onDirtyChange }: {
  /** Reports (rulesDirty, targetsDirty) upward for the page's sticky
   *  unsaved-sections bar. */
  onDirtyChange?: (rules: boolean, targets: boolean) => void;
}) {
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
    onDirtyChange?.(rulesDirty, targetsDirty);
    if (!rulesDirty && !targetsDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [rulesDirty, targetsDirty, onDirtyChange]);

  useEffect(() => {
    getIncentivesConfig()
      .then((res) => {
        if (res.config) {
          const loaded = { ...EMPTY, ...res.config };
          setCfg(loaded);
          setConfigured(true);
          rulesBaseline.current = JSON.stringify(loaded);
          const at = (res.config as { updated_at?: string }).updated_at;
          if (at) setLastSaved(String(at).slice(0, 16).replace('T', ' '));
        }
        setCompanies(res.companies);
        setStats(res.company_stats ?? {});
        const map: Record<number, string> = {};
        for (const tg of res.targets) map[tg.company_id] = String(tg.weekly_gross_target);
        setTargets(map);
        targetsBaseline.current = normTargets(map);
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  const hasAnyTarget = Object.values(targets).some((v) => v.trim() !== '');
  const [lastSaved, setLastSaved] = useState('');
  const [stats, setStats] = useState<Record<string, { trucks: number; avg_truck_week: number }>>({});
  // The dry-run band: candidate rules re-priced against the latest run.
  // Cleared on ANY further edit — a stale preview is a wrong promise.
  const [preview, setPreview] = useState<RulesPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Per-row dirty accent + linter inputs — parsed once per render
  // (tiny arrays; the baseline only changes on load/save).
  const baseTiers: IncentiveTier[] = (() => {
    try { return (JSON.parse(rulesBaseline.current) as IncentiveConfig).tiers ?? []; }
    catch { return []; }
  })();
  const tierDirty = (i: number) =>
    JSON.stringify(cfg.tiers[i]) !== JSON.stringify(baseTiers[i]);

  const discardRules = () => {
    try { setCfg(JSON.parse(rulesBaseline.current) as IncentiveConfig); }
    catch { setCfg(EMPTY); }
    setRulesDirty(false);
  };
  const discardTargets = () => {
    try {
      const saved = JSON.parse(targetsBaseline.current) as Record<string, string>;
      setTargets(Object.fromEntries(
        Object.entries(saved).map(([k, v]) => [Number(k), String(v)])));
    } catch { setTargets({}); }
    setTargetsDirty(false);
  };

  // The rules LINTER (ladder only): a dead row and a target-requirement
  // inversion are silent policy bugs on a money config — say them
  // BEFORE save, never block (the engine allows both shapes).
  const deadRows = new Set<number>();
  const lint: string[] = (() => {
    if (cfg.model !== 'ladder') return [];
    const out: string[] = [];
    const rpm = (x: IncentiveTier) => Number(x.min_rpm ?? Number.NEGATIVE_INFINITY);
    const gro = (x: IncentiveTier) => Number(x.min_weekly_gross ?? Number.NEGATIVE_INFINITY);
    cfg.tiers.forEach((tj, j) => {
      const dominator = cfg.tiers.findIndex((tk, k) => k !== j
        && Number(tk.pct) >= Number(tj.pct)
        && (!tk.requires_target || !!tj.requires_target)
        && rpm(tk) <= rpm(tj) && gro(tk) <= gro(tj));
      if (dominator >= 0 && dominator < j) {
        deadRows.add(j);
        out.push(t('kpi_config.lint_dead',
          'Row {{j}} never decides a payout — row {{k}} pays the same or more with easier conditions.',
          { j: j + 1, k: dominator + 1 }));
      }
    });
    if (!hasAnyTarget && cfg.tiers.some((x) => x.requires_target)) {
      out.push(t('kpi_config.lint_no_targets',
        'No company has a weekly target — every “target met” row is unearnable and runs pay 0% until targets are saved below.'));
    }
    const maxNoTarget = Math.max(Number.NEGATIVE_INFINITY,
      ...cfg.tiers.filter((x) => !x.requires_target).map((x) => Number(x.pct)));
    const minWithTarget = Math.min(Number.POSITIVE_INFINITY,
      ...cfg.tiers.filter((x) => x.requires_target).map((x) => Number(x.pct)));
    if (maxNoTarget > minWithTarget) {
      out.push(t('kpi_config.lint_inversion',
        'Some rows WITHOUT “target met” pay more than rows that require it — near the boundary, missing the target can pay more than meeting it. Intentional for high-RPM rewards; worth knowing.'));
    }
    return out;
  })();

  const setCfgDirty = (next: IncentiveConfig) => {
    setCfg(next);
    setRulesDirty(JSON.stringify(next) !== rulesBaseline.current);
    setPreview(null);
  };

  const runPreview = async () => {
    setPreviewing(true);
    try {
      setPreview(await previewIncentiveRules(cfg));
    } catch (e) {
      // The engine's own message (bad tier shape etc.) — verbatim.
      toast.error(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setPreviewing(false);
    }
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
        <Loader2 className="animate-spin text-muted-foreground size-4.5" />
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
                    {step.done ? <Check className="size-3" /> : i + 1}
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
                <ArrowRight className="size-3.5" />
              </Link>
            </li>
          </ol>
        </div>
      )}

      {/* ── Incentive rules: model + policy + tiers ─────────────────
          ONE card because they are ONE PUT — the save's enclosure is
          its scope, and a save floating between two cards belongs to
          neither.  Sub-sections carry caps labels. */}
      <section id="cfg-rules" className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">
            {t('kpi_config.rules_title', 'Incentive rules')}
          </h2>
          {lastSaved && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {t('kpi_config.last_saved', 'Last saved {{d}}', { d: lastSaved })}
            </span>
          )}
        </div>
        {/* Both sub-sections get the SAME anatomy: a hairline seam +
            padding — one seam rule, and the title band above is bounded
            by a line, not by air alone. */}
        <div className="border-t border-border pt-4 space-y-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t('kpi_config.model_title', 'Model & policy')}
        </h3>
        {/* ONE column grid for both policy rows — per-field widths put
            column 2 sixty-four pixels apart between stacked rows. */}
        <div className="grid grid-cols-1 items-end gap-4 sm:grid-cols-[18rem_14rem_14rem]">
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
            <span className="block text-muted-foreground">{t('kpi_config.cadence2', 'Payout period')}</span>
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
        <div className="grid grid-cols-1 items-end gap-4 sm:grid-cols-[18rem_14rem_14rem]">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.cap3', 'Manual exception cap')}</span>
            {/* Word placeholders, not example numbers: empty IS a real
                state (no cap / no floor), and a grey "7000" reads as a
                filled value at a glance.  Units live AT the input —
                one adornment convention page-wide. */}
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3" aria-hidden />
              <NumField value={cfg.exception_cap_pct} onChange={(v) => patchCfg({ exception_cap_pct: v })} placeholder={t('kpi_config.none', 'none')} />
              <span className="text-muted-foreground">%</span>
            </span>
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.floor_gross4', '0% floor — truck gross/wk')}</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3 text-muted-foreground">$</span>
              <NumField value={cfg.floor_weekly_gross} onChange={(v) => patchCfg({ floor_weekly_gross: v })} placeholder={t('kpi_config.none', 'none')} />
            </span>
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_config.floor_rpm3', '0% floor — RPM')}</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3" aria-hidden />
              <NumField value={cfg.floor_rpm} onChange={(v) => patchCfg({ floor_rpm: v })} placeholder={t('kpi_config.none', 'none')} width="w-20" />
              <span className="text-muted-foreground">$/mi</span>
            </span>
          </label>
        </div>
        {((cfg.floor_weekly_gross == null) !== (cfg.floor_rpm == null)) && (
          <p className={`text-xs ${toneClasses('warn')} border px-2 py-1 rounded inline-block`} role="status">
            {t('kpi_config.one_floor',
              'Only one floor is set — the 0% rule needs BOTH and is inactive until the other is filled.')}
          </p>
        )}
        <p className="text-xs text-muted-foreground max-w-prose">
          {t('kpi_config.floor_note',
            'The 0% floors fire together: a truck below BOTH is removed from pay (one alone does nothing). The manual cap limits hand-entered run exceptions — refused above it, never clamped — and is unrelated to tier percentages. Targets and floors are always per WEEK; longer periods prorate day by day.')}
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
              {t('kpi_config.tiers_hint2',
                'A truck earns every row whose conditions it meets — the highest % pays; each dispatcher is paid the sum of their trucks. An empty condition means no requirement; “weekly target met” compares against the ')}
              <button type="button"
                onClick={() => document.getElementById('cfg-targets')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                className="underline underline-offset-4 hover:text-foreground">
                {t('kpi_config.tiers_hint_link', 'company weekly targets')}
              </button>
              {t('kpi_config.tiers_hint_tail', ' set below.')}
            </p>
          </div>
          {/* The only action that advances an unconfigured account —
              filled, so it out-weighs the (inert) saves below. */}
          <Button size="sm" variant="outline" onClick={() => {
            // One step ABOVE the current top — never an any/any catch-all
            // that would pay every truck (the audit's verified footgun).
            let next = fresh(cfg.model);
            if (cfg.model === 'ladder' && cfg.tiers.length > 0) {
              const top = cfg.tiers.reduce((a, b) =>
                Number(b.pct) > Number(a.pct) ? b : a);
              const topRpm = Math.max(...cfg.tiers.map((x) => Number(x.min_rpm ?? 0)));
              next = { requires_target: !!top.requires_target,
                min_rpm: Math.round((topRpm + 0.25) * 100) / 100,
                pct: Math.round((Number(top.pct) + 0.25) * 100) / 100 };
            }
            const at = cfg.tiers.length;
            setCfgDirty({ ...cfg, tiers: [...cfg.tiers, next] });
            requestAnimationFrame(() => {
              const el = document.querySelector<HTMLInputElement>(
                `[data-tier-row="${at}"] input`);
              el?.focus();
              el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
          }}>
            <Plus /> {t('kpi_config.add_tier', 'Add tier')}
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
              <Wand2 className="mr-1.5" />
              {t('kpi_config.preset', 'Start from a common ladder')}
            </Button>
            <p className="text-xs text-muted-foreground max-w-prose">
              {t('kpi_config.preset_note',
                'A real carrier’s ladder: 1% for RPM ≥ 2.0 even below target, 1% at target, 1.5% at target + RPM ≥ 2.0, 2% at target + RPM ≥ 2.5 — plus a 2% exception cap and $7,000 / 1.9 RPM floors. A starting point to edit, not a recommendation.')}
            </p>
          </div>
        ) : (
          <>
          {lint.length > 0 && (
            <ul className="space-y-1" role="status" aria-live="polite">
              {lint.map((w, i) => (
                <li key={i} className={`text-xs ${toneClasses('warn')} border px-2 py-1 rounded`}>
                  {w}
                </li>
              ))}
            </ul>
          )}
          <ul className="border-t border-border">
            {cfg.tiers.map((tier, i) => (
              <li key={i} data-tier-row={i}
                className={`flex flex-wrap items-center gap-3 py-2 text-sm border-b border-b-border last:border-b-0 border-l-2 ${
                  tierDirty(i) ? 'border-l-warn-bd' : 'border-l-transparent'}`}>
                <span className="w-5 text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                {cfg.model === 'ladder' && (
                  <>
                    <label className="inline-flex cursor-pointer items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors">
                      <input
                        type="checkbox"
                        checked={!!tier.requires_target}
                        onChange={(e) => setTier(i, { requires_target: e.target.checked })}
                        className="size-4 cursor-pointer accent-primary align-middle"
                        aria-label={t('kpi_config.requires_target', 'Requires weekly target met')}
                      />
                      {t('kpi_config.target_met', 'weekly target met')}
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                      {t('kpi_config.min_gross2', 'truck gross/wk ≥')}
                      <NumField value={tier.min_weekly_gross} onChange={(v) => setTier(i, { min_weekly_gross: v })} placeholder={t('kpi_config.any', 'any')} width="w-28" />
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
                  {deadRows.has(i) && (
                    <span className={`text-xs ${toneClasses('warn')} border px-1.5 py-0.5 rounded`}>
                      {t('kpi_config.never_applies', 'never applies')}
                    </span>
                  )}
                  <label className="inline-flex items-center gap-1.5 text-muted-foreground">
                    →
                    <NumField value={tier.pct} onChange={(v) => setTier(i, { pct: v ?? 0 })} width="w-20"
                      ariaLabel={t('kpi_config.pct_aria', 'Payout %')} />
                    %
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const removed = tier;
                      const at = i;
                      setCfgDirty({ ...cfg, tiers: cfg.tiers.filter((_, j) => j !== i) });
                      toast(t('kpi_config.tier_removed2',
                        'Tier {{n}} removed ({{pct}}%)',
                        { n: at + 1, pct: removed.pct }), {
                        action: {
                          label: t('common.undo', 'Undo'),
                          onClick: () => setCfgDirty({
                            ...cfg,
                            tiers: [...cfg.tiers.slice(0, at), removed,
                              ...cfg.tiers.slice(at)].filter((x, j, arr) =>
                              arr.indexOf(x) === j),
                          }),
                        },
                      });
                    }}
                    aria-label={t('kpi_config.remove_tier', 'Remove tier')}
                    className="inline-flex size-7 items-center justify-center rounded text-muted-foreground hover:text-danger hover:bg-muted min-h-tap min-w-tap"
                  >
                    <X className="size-3.5" />
                  </button>
                </span>
              </li>
            ))}
          </ul>
          </>
        )}
        </div>

        {/* The effect BEFORE the ask: candidate rules re-priced against
            the latest run.  Nothing is written by a preview.  Rendered
            ALWAYS (disabled while clean) so the first edit doesn't
            insert a control into the flow. */}
        {(
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" onClick={runPreview}
              disabled={previewing || !configured}>
              {previewing && <Loader2 className="animate-spin mr-1.5" />}
              {rulesDirty
                ? t('kpi_config.preview_btn2', 'Preview payout change')
                : t('kpi_config.preview_btn3', 'Preview these rules on the latest run')}
            </Button>
            {preview && !preview.available && (
              <span className="text-xs text-muted-foreground">
                {t('kpi_config.preview_none', 'No runs yet to preview against.')}
              </span>
            )}
            {preview?.available && (
              /* The BASELINE is the run's current (snapshot) pricing —
                 without naming it, a delta reads as "the effect of my
                 last edit", which it is not when the saved rules
                 already differ from the run's snapshot. */
              <span className="text-sm tabular-nums" role="status" aria-live="polite">
                {t('kpi_config.preview_line2',
                  'Run {{a}} – {{b}} ({{status}}) currently pays ${{cur}} — under these rules ',
                  {
                    a: preview.period_start, b: preview.period_end,
                    status: preview.status,
                    cur: (preview.current_total ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 }),
                  })}
                <span className="font-medium">
                  ${(preview.candidate_total ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
                <span className={`ml-1 font-medium ${
                  (preview.delta ?? 0) >= 0 ? toneText('ok') : toneText('danger')}`}>
                  ({(preview.delta ?? 0) >= 0 ? '+' : '−'}$
                  {Math.abs(preview.delta ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })})
                </span>
                <span className="ml-1 text-xs text-muted-foreground">
                  {t('kpi_config.preview_moved', '· {{moved}} of {{rows}} trucks change %',
                    { moved: preview.moved_rows, rows: preview.rows })}
                </span>
              </span>
            )}
          </div>
        )}

        {/* When an incentive change takes effect — the one timing fact
            this page never stated while a draft sat open next door. */}
        <p className="text-xs text-muted-foreground max-w-prose">
          {t('kpi_config.timing_note',
            'Saved rules apply to runs created AFTER the save. Open drafts keep their snapshot — use the board’s “Recreate draft” to re-price one under the new rules.')}
        </p>

        {/* One save for the whole card — model, policy and tiers are
            ONE PUT, so the button sits inside the one card that IS its
            scope and takes the CARD'S name: all three saves on the page
            read "Save <card title>" (same grammar as Targets below).
            border-t: the commit bar is the CARD's, not the last
            sub-region's — bounded by a rule, not by 16px of air. */}
        <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
          {rulesDirty && (
            <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
              {t('kpi_config.unsaved', 'Unsaved changes')}
            </span>
          )}
          {/* Weight tracks actionability: outline while inert, filled
              the moment there is something to save. */}
          {rulesDirty && (
            <button type="button" onClick={discardRules}
              className="py-1 -my-1 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground min-h-tap">
              {t('kpi_config.discard', 'Discard changes')}
            </button>
          )}
          <Button variant={rulesDirty ? 'default' : 'outline'} onClick={requestSaveRules} disabled={saving || !rulesDirty}>
            {saving && <Loader2 className="animate-spin mr-1.5" />}
            {t('kpi_config.save_rules', 'Save incentive rules')}
          </Button>
        </div>
      </section>

      {/* ── Per-company weekly targets ──────────────────────────── */}
      <section id="cfg-targets" className="bg-card border border-border rounded-xl p-5 space-y-3">
        <h2 className="text-base font-semibold">{t('kpi_config.targets_title2', 'Company weekly targets')}</h2>
        <p className="text-xs text-muted-foreground max-w-prose">
          {t('kpi_config.targets_note',
            'One weekly target per company — the same target the tiers’ “weekly target met” checks against. It prorates by active days: $8,000/week over 19 active days is a $21,714.29 target. A company without one resolves 0% until it is set.')}
        </p>
        {/* border-t: the header+description band ends at a line, so the
            first company row cannot read as part of the header group. */}
        <div className="flex items-center justify-end gap-2">
          <span className="text-xs text-muted-foreground">{t('kpi_config.set_all', 'Set all to')}</span>
          <NumField value={null} onChange={(v) => {
            if (v == null) return;
            const next: Record<number, string> = {};
            for (const co of companies) next[co.id] = String(v);
            setTargets(next);
            setTargetsDirty(normTargets(next) !== targetsBaseline.current);
          }} placeholder="8000" width="w-28" />
        </div>
        {companies.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t('kpi_config.no_companies',
              'No companies yet — companies arrive from your TMS integration, and each gets its target here.')}
          </p>
        )}
        <ul className="divide-y divide-border border-t border-border">
          {companies.map((co) => (
            <li key={co.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="text-foreground">{co.name} <span className="text-muted-foreground">({co.code})</span>
                {stats[co.code] && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {t('kpi_config.co_stats', '{{n}} trucks · avg ${{avg}}/truck-wk',
                      { n: stats[co.code].trucks,
                        avg: Math.round(stats[co.code].avg_truck_week).toLocaleString() })}
                  </span>
                )}
                {(targets[co.id] ?? '').trim() === '' && (
                  <span className={`ml-2 text-xs ${toneClasses('warn')} px-1.5 py-0.5 rounded`}>
                    {t('kpi_config.no_target_flag', 'no target — trucks resolve 0%')}
                  </span>
                )}
              </span>
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
        <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
          {targetsDirty && (
            <span className={`text-xs ${toneClasses('warn')} px-2 py-0.5 rounded`}>
              {t('kpi_config.unsaved', 'Unsaved changes')}
            </span>
          )}
          {targetsDirty && (
            <button type="button" onClick={discardTargets}
              className="py-1 -my-1 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground min-h-tap">
              {t('kpi_config.discard', 'Discard changes')}
            </button>
          )}
          <Button variant={targetsDirty ? 'default' : 'outline'} onClick={saveTargets} disabled={saving || !targetsDirty}>
            {saving && <Loader2 className="animate-spin mr-1.5" />}
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
              {saving && <Loader2 className="animate-spin mr-1.5" />}
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
              'The {{count}} tier(s) you entered belong to the current model and cannot carry over — the models use different fields. Switching clears them (Discard changes restores them until you save).',
              { count: cfg.tiers.length })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingModel(null)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={() => {
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
