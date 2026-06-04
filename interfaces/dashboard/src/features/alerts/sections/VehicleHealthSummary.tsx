/**
 * Fleet hero strip — three counts derived client-side from the same
 * /alerts data the rest of the page is rendering.
 *
 * Open faults, mechanical health, and low-fuel are the three states
 * the Fleet persona triages first.  All three are computed over the
 * CURRENT filter window (the chips above) so changing the date range
 * or vehicle search re-aggregates immediately — no separate request,
 * no stale snapshot.
 *
 * Clicking a card narrows the queue to that alert type via the URL
 * (the chip row above the queue reads the same URL and updates on
 * the next render).  Re-clicking an active card clears back to
 * "all" so the cards function as toggles, matching FilterChips.
 *
 * "Maintenance due" is intentionally NOT here — maintenance alerts
 * are posted via the lite forum-routing path, not stored in
 * alert_history, so a client-side count over /alerts always returns
 * 0.  When the maintenance-task aggregate endpoint lands, add a
 * fourth card sourced from /api/maintenance/summary.
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, HeartPulse, Fuel } from 'lucide-react';
import { KpiCard } from '../../../components/shell';
import { useAlertsQuery } from '../_shared/useAlertsQuery';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import type {
  Alert, AlertsResponse, VehiclesAlertsResponse,
} from '../../../types';
import type { AlertType } from '../personaConfig';

interface Counts {
  faults: number;
  health: number;
  lowFuel: number;
}

function emptyCounts(): Counts {
  return { faults: 0, health: 0, lowFuel: 0 };
}

function tallyFromAlerts(alerts: Alert[]): Counts {
  const c = emptyCounts();
  for (const a of alerts) {
    const t = a.alert_type;
    if (t === 'fault' || t === 'faults') c.faults++;
    else if (t === 'health') c.health++;
    else if (t === 'fuel') c.lowFuel++;
  }
  return c;
}

function tallyFromResponse(
  data: AlertsResponse | VehiclesAlertsResponse | undefined,
): Counts {
  if (!data) return emptyCounts();
  if ('vehicles' in data && Array.isArray((data as VehiclesAlertsResponse).vehicles)) {
    return (data as VehiclesAlertsResponse).vehicles.reduce<Counts>(
      (acc, v) => {
        const sub = tallyFromAlerts(v.alerts ?? []);
        acc.faults  += sub.faults;
        acc.health  += sub.health;
        acc.lowFuel += sub.lowFuel;
        return acc;
      },
      emptyCounts(),
    );
  }
  if ('alerts' in data) {
    return tallyFromAlerts((data as AlertsResponse).alerts ?? []);
  }
  return emptyCounts();
}

export default function VehicleHealthSummary() {
  const { t } = useTranslation();
  const { data, isLoading } = useAlertsQuery();
  const { typeFilter, setTypeFilter } = useAlertsFilters();
  const counts = useMemo(() => tallyFromResponse(data), [data]);

  const setOrClear = (type: AlertType) => () =>
    setTypeFilter(typeFilter === type ? 'all' : type);
  const clickToFilter = t('alerts.vehicle_health.click_to_filter');

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      <KpiCard
        label={t('alerts.vehicle_health.open_faults')}
        value={isLoading ? '…' : counts.faults}
        tone={counts.faults > 0 ? 'critical' : 'default'}
        icon={AlertTriangle}
        onClick={setOrClear('fault')}
        hint={typeFilter === 'fault'
          ? t('alerts.vehicle_health.showing_only_faults')
          : clickToFilter}
      />
      <KpiCard
        label={t('alerts.vehicle_health.mechanical_health')}
        value={isLoading ? '…' : counts.health}
        tone={counts.health > 0 ? 'warning' : 'default'}
        icon={HeartPulse}
        onClick={setOrClear('health')}
        hint={typeFilter === 'health'
          ? t('alerts.vehicle_health.showing_only_health')
          : clickToFilter}
      />
      <KpiCard
        label={t('alerts.vehicle_health.low_fuel')}
        value={isLoading ? '…' : counts.lowFuel}
        tone={counts.lowFuel > 0 ? 'warning' : 'default'}
        icon={Fuel}
        onClick={setOrClear('fuel')}
        hint={typeFilter === 'fuel'
          ? t('alerts.vehicle_health.showing_only_fuel')
          : clickToFilter}
      />
    </div>
  );
}
