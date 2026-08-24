/**
 * KPI configuration — the page the cog opens.
 *
 * KPI's config OUTGREW the dialog when incentives arrived: a model
 * picker, a dynamic tier table, policy knobs and a per-company targets
 * table beside the original six grading thresholds.  The gear rule
 * already covers this case — "config that is a PAGE, not a panel"
 * (FeatureConfigGear's `to` mode, written for Scorecards) — so KPI's cog
 * now navigates here instead of opening a popup.  Same icon, same slot,
 * same permission; only the destination grew.
 *
 * Two sections, deliberately in this order: GRADES first (the analytics
 * everyone sees), INCENTIVES second (the money).  They share inputs but
 * are different products — changing a grade threshold embarrasses
 * nobody's paycheck; changing a tier does.
 */
import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ArrowLeft, Gauge } from 'lucide-react';
import { toneClasses } from '../../../lib/status';
import { PageHeader } from '../../../components/shell';
import KpiConfigPanel from './KpiConfigPanel';
import IncentiveEditor from './IncentiveEditor';

export default function KpiConfiguration() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // The page-level dirty map: three sections, three saves — the sticky
  // bar below makes an off-screen dirty section impossible to forget.
  // (BrowserRouter has no navigation blocker; this bar + beforeunload
  // is the documented ceiling.)
  const [dirtySections, setDirtySections] = useState({
    grading: false, rules: false, targets: false,
  });
  const onPanelDirty = useCallback((d: boolean) =>
    setDirtySections((m) => (m.grading === d ? m : { ...m, grading: d })), []);
  const onEditorDirty = useCallback((rules: boolean, targets: boolean) =>
    setDirtySections((m) => (m.rules === rules && m.targets === targets
      ? m : { ...m, rules, targets })), []);
  const dirtyList = [
    dirtySections.grading && { id: 'cfg-grading', label: t('kpi_config.grades_title', 'Grading thresholds') },
    dirtySections.rules && { id: 'cfg-rules', label: t('kpi_config.rules_title', 'Incentive rules') },
    dirtySections.targets && { id: 'cfg-targets', label: t('kpi_config.targets_title2', 'Company weekly targets') },
  ].filter(Boolean) as { id: string; label: string }[];

  return (
    <div>
      {/* The way BACK — this page is reached via the run page's gear,
          and a config page with no exit strands the person who came to
          change one number. */}
      <Link to="/kpi/dispatch"
        className="mb-1.5 inline-flex items-center gap-1 py-0.5 min-h-tap text-sm text-muted-foreground hover:text-foreground transition">
        <ArrowLeft className="size-3.5" />
        {t('kpi_config.back', 'Dispatch KPI')}
      </Link>
      <PageHeader
        icon={Gauge}
        /* Named after the SECTION it configures — with per-role KPI
           pages, a generic "KPI configuration" stops saying WHOSE bars
           are being moved.  Every future section repeats the pattern
           (KPI configuration · Fleet, · Safety, …). */
        title={t('kpi_config.page_title2', 'KPI configuration · Dispatch')}
        description={t(
          'kpi_config.page_desc2',
          'The DISPATCH section only — grading thresholds for the A–D view and the dispatcher incentive rules. Account-wide: changing either changes it for every viewer. Other sections will carry their own configuration pages.',
        )}
      />

      {/* 32px between top-level cards — clearly above the 12–16px
          rhythms inside them. */}
      <div className="space-y-8">
        <section id="cfg-grading" className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h2 className="text-base font-semibold">
            {t('kpi_config.grades_title', 'Grading thresholds')}
          </h2>
          <KpiConfigPanel
            onSaved={() => qc.invalidateQueries({ queryKey: ['kpi-dispatchers'] })}
            onDirtyChange={onPanelDirty}
          />
        </section>

        <IncentiveEditor onDirtyChange={onEditorDirty} />
      </div>

      {/* Sticky unsaved-sections bar: a dirty card scrolled off-screen
          is the silent-discard trap — this stays visible until every
          section is saved or discarded. */}
      {dirtyList.length > 0 && (
        /* A max-content pill anchored LEFT: every commit control on the
           page is right-aligned, so this can never occlude one at any
           scroll position — the full-width strip covered Save 100% in
           a 65px scroll band no end-padding could fix. */
        <div className="sticky bottom-2 z-30 mt-6 inline-flex max-w-fit flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-md">
          <span className={`text-xs ${toneClasses('warn')} border px-2 py-0.5 rounded-md`}>
            {t('kpi_config.dirty_n', '{{n}} unsaved', { n: dirtyList.length })}
          </span>
          {dirtyList.map((d) => (
            <button key={d.id} type="button"
              onClick={() => document.getElementById(d.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              className="rounded-md border border-border bg-card px-2 py-0.5 text-xs text-foreground hover:border-ring transition min-h-tap">
              {d.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
