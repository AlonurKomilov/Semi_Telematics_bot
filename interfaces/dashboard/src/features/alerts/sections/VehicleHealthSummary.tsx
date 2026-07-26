/**
 * Fleet hero strip — open-alert counters above the board.
 *
 * The counts are TOTALS over the whole open queue, fetched from
 * /alerts/pending/by-type.  They deliberately do NOT follow the board's
 * filters.  They used to: all three were tallied client-side from the
 * page's own response, so filtering to Fuel drove "Open faults" to 0 in
 * calm grey while hundreds of faults were open — a false all-clear on a
 * safety surface.  Even unfiltered, a client tally only ever saw the
 * first page_size rows of a longer queue.  A total that moves when you
 * filter is not a total, so the number now stays put and SELECTION is
 * drawn as a ring on the card instead.
 *
 * Clicking a card narrows the queue below to that type via the URL;
 * clicking the active card clears back to "all", matching FilterChips.
 *
 * A card renders only when the server reported its type — the response
 * omits types the active view may not see, and an omitted type must not
 * render as 0 ("nothing wrong" ≠ "not yours to see").  This is why the
 * low-fuel card is absent for Fleet: ``fuel`` routes to the Dispatcher
 * persona, so a Fleet view never had fuel alerts to count.
 *
 * "Maintenance due" is intentionally NOT here — maintenance alerts are
 * posted via the lite forum-routing path, not stored in alert_history,
 * so any count over the alert queue returns 0.  When the maintenance-task
 * aggregate endpoint lands, add a card sourced from it.
 */
import { useTranslation } from 'react-i18next';
import { AlertTriangle, HeartPulse, Fuel } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { KpiCard } from '../../../components/shell';
import { useAlertTypeCounts } from '../_shared/useAlertTypeCounts';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import type { AlertType } from '../personaConfig';

/** One card per concept, not per wire key: ``fault`` and ``faults`` are
 *  live aliases for the same thing (both appear in the Fleet persona's
 *  type set), so the card sums every key it owns and filters by the
 *  canonical one the chip row uses. */
interface CardSpec {
  labelKey: string;
  keys: string[];
  filterAs: AlertType;
  icon: LucideIcon;
  tone: 'critical' | 'warning';
  activeKey: string;
}

const CARDS: CardSpec[] = [
  {
    labelKey: 'alerts.vehicle_health.open_faults',
    keys: ['fault', 'faults'],
    filterAs: 'fault',
    icon: AlertTriangle,
    tone: 'critical',
    activeKey: 'alerts.vehicle_health.showing_only_faults',
  },
  {
    labelKey: 'alerts.vehicle_health.mechanical_health',
    keys: ['health'],
    filterAs: 'health',
    icon: HeartPulse,
    tone: 'warning',
    activeKey: 'alerts.vehicle_health.showing_only_health',
  },
  {
    labelKey: 'alerts.vehicle_health.low_fuel',
    keys: ['fuel'],
    filterAs: 'fuel',
    icon: Fuel,
    tone: 'warning',
    activeKey: 'alerts.vehicle_health.showing_only_fuel',
  },
];

export default function VehicleHealthSummary() {
  const { t } = useTranslation();
  const { counts, isLoading } = useAlertTypeCounts();
  const { typeFilter, setTypeFilter } = useAlertsFilters();

  const setOrClear = (type: AlertType) => () =>
    setTypeFilter(typeFilter === type ? 'all' : type);
  const clickToFilter = t('alerts.vehicle_health.click_to_filter');

  // Present = the server counted this type for this view.  While the
  // request is in flight nothing is known yet, so show the full strip
  // with placeholders rather than guessing which cards will survive.
  const visible = counts
    ? CARDS.filter((c) => c.keys.some((k) => k in counts))
    : CARDS;

  if (!isLoading && visible.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      {visible.map((c) => {
        const value = counts
          ? c.keys.reduce((sum, k) => sum + (counts[k] ?? 0), 0)
          : undefined;
        const selected = typeFilter === c.filterAs;
        return (
          <KpiCard
            key={c.filterAs}
            label={t(c.labelKey)}
            value={isLoading ? '…' : value}
            tone={(value ?? 0) > 0 ? c.tone : 'default'}
            icon={c.icon}
            onClick={setOrClear(c.filterAs)}
            selected={selected}
            hint={selected ? t(c.activeKey) : clickToFilter}
          />
        );
      })}
    </div>
  );
}
