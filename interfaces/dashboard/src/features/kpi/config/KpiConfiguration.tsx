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
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Gauge } from 'lucide-react';
import { PageHeader } from '../../../components/shell';
import KpiConfigPanel from './KpiConfigPanel';
import IncentiveEditor from './IncentiveEditor';

export default function KpiConfiguration() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  return (
    <div>
      <PageHeader
        icon={Gauge}
        title={t('kpi_config.page_title', 'KPI configuration')}
        description={t(
          'kpi_config.page_desc',
          'Account-wide: grading thresholds for the A–D view, and the dispatcher incentive rules. Changing either changes it for everyone.',
        )}
      />

      <div className="space-y-6">
        <section className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h2 className="text-base font-semibold">
            {t('kpi_config.grades_title', 'Grading thresholds')}
          </h2>
          <KpiConfigPanel
            onSaved={() => qc.invalidateQueries({ queryKey: ['kpi-dispatchers'] })}
          />
        </section>

        <IncentiveEditor />
      </div>
    </div>
  );
}
